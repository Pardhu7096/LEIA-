"""End-to-end /chat tests for hybrid search, AI fallback and the cost guard (local DB, mocked Groq)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import create_access_token  # noqa: E402
from test_crm import database, staff  # noqa: E402,F401  (shared isolated-database fixture)


@pytest.fixture
def hybrid(database, monkeypatch):  # noqa: F811
    import ai_guard
    import auth
    import main
    import rate_limits

    for module in (main, auth, ai_guard, rate_limits):
        monkeypatch.setattr(module, "get_connection", database)
    monkeypatch.setattr(main, "limit_chat", lambda user: None)
    monkeypatch.delenv("LEIA_SEARCH_ROUTER", raising=False)
    monkeypatch.setenv("LEIA_AI_BACKOFF_SECONDS", "0")
    ai_guard.extraction_cache.clear()
    with database() as connection:
        connection.execute("DELETE FROM ai_usage_periods")
        connection.execute("DELETE FROM request_limits")
        # The isolated database is shared by this module's tests: start from this module's rows only.
        connection.execute("DELETE FROM properties WHERE title LIKE 'Hybrid %'")

    agent = staff(database, "agent")
    customer = staff(database, "customer")
    tag = uuid4().hex[:8]
    ids = {}

    def insert(key, **values):
        row = {"title": f"Hybrid {key} {tag}", "property_type": "flat", "bedrooms": 3, "price": 6000000,
               "area_sqft": 1300, "locality": "Chinthala Agraharam", "city": f"HybridCity{tag}",
               "facing": "north", "status": "approved", "availability": "available",
               "car_parking_spaces": None, "parking_type": "unknown", "power_backup": "unknown", **values}
        pid = uuid4()
        with database() as connection:
            connection.execute(
                """INSERT INTO properties (id, created_by, title, property_type, bedrooms, price, area_sqft,
                       locality, city, facing, status, availability, car_parking_spaces, parking_type,
                       power_backup, reviewed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)""",
                (pid, agent["id"], row["title"], row["property_type"], row["bedrooms"], row["price"],
                 row["area_sqft"], row["locality"], row["city"], row["facing"], row["status"],
                 row["availability"], row["car_parking_spaces"], row["parking_type"], row["power_backup"]))
        ids[key] = str(pid)

    insert("small", area_sqft=1100, price=5000000, car_parking_spaces=1, parking_type="covered")
    insert("mid", area_sqft=1400, price=6800000, power_backup="full_home")
    insert("big", area_sqft=1700, price=9000000, parking_type="open")
    insert("draft", status="draft")
    insert("sold", availability="sold")

    ai = SimpleNamespace(calls=[], behaviour=None)

    def create(**kwargs):
        ai.calls.append(kwargs)
        return ai.behaviour(**kwargs)

    monkeypatch.setattr(main.client.chat.completions, "create", create)
    token = create_access_token(customer["id"])["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    http = TestClient(main.app)
    conversation = http.post("/conversations", headers=headers).json()["id"]

    def chat(message):
        response = http.post("/chat", headers=headers, json={"message": message, "conversation_id": conversation})
        assert response.status_code == 200, response.text
        return response.json()

    return SimpleNamespace(http=http, chat=chat, ai=ai, ids=ids, database=database, headers=headers,
                           conversation=conversation, main=main)


def listing_ids(result):
    return {p["id"] for p in result["properties"]}


def test_structured_query_uses_sql_filters_without_groq(hybrid):
    result = hybrid.chat("3 BHK flats between 1,200 and 1,500 sq ft in Chintalaagraharam")
    assert listing_ids(result) >= {hybrid.ids["mid"]}
    assert hybrid.ids["small"] not in listing_ids(result) and hybrid.ids["big"] not in listing_ids(result)
    assert not hybrid.ai.calls
    hybrid.chat("start over")  # follow-ups keep earlier filters (the size range) until reset
    with_parking = hybrid.chat("flats with parking in Chinthalagraharam")
    assert {hybrid.ids["small"], hybrid.ids["big"]} <= listing_ids(with_parking)
    assert hybrid.ids["mid"] not in listing_ids(with_parking)
    hybrid.chat("start over")
    power = hybrid.chat("flats with power backup")
    assert hybrid.ids["mid"] in listing_ids(power) and hybrid.ids["small"] not in listing_ids(power)
    cheapest = hybrid.chat("show all properties")
    assert hybrid.ids["draft"] not in listing_ids(cheapest) and hybrid.ids["sold"] not in listing_ids(cheapest)
    hybrid.chat("start over")
    sorted_result = hybrid.chat("3 bhk in Chinthalagraharam sorted by price")
    prices = [p["price"] for p in sorted_result["properties"]]
    assert prices == sorted(prices)
    assert not hybrid.ai.calls


def test_provider_failure_falls_back_to_ordinary_search(hybrid):
    def broken(**kwargs):
        raise RuntimeError("provider down")

    hybrid.ai.behaviour = broken
    result = hybrid.chat("3 bhk near the lake in Chintalaagraharam")
    assert len(hybrid.ai.calls) == 1  # non-retryable error: no retry storm
    assert "couldn’t interpret part" in result["reply"]
    assert result["filters"]["bedrooms"] == 3 and result["filters"]["location"] == "Chinthalagraharam"
    assert hybrid.ids["mid"] in listing_ids(result)
    with hybrid.database() as connection:
        day = connection.execute("SELECT * FROM ai_usage_periods WHERE period LIKE 'day:%'").fetchone()
    assert day["reserved_micro_inr"] == 0


def test_ai_answer_is_schema_validated_and_metered(hybrid):
    def answer(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                {"intent": "search", "bedrooms": 3, "location": "Chinthalagraharam"})))],
            usage=SimpleNamespace(prompt_tokens=1100, completion_tokens=120,
                                  completion_tokens_details=SimpleNamespace(reasoning_tokens=40)))

    hybrid.ai.behaviour = answer
    result = hybrid.chat("a calm 3 bhk home for my parents in Chintalaagraharam")
    assert hybrid.ids["mid"] in listing_ids(result)
    kwargs = hybrid.ai.calls[0]
    assert kwargs["reasoning_effort"] == "low" and kwargs["max_completion_tokens"] <= 600
    with hybrid.database() as connection:
        day = connection.execute("SELECT * FROM ai_usage_periods WHERE period LIKE 'day:%'").fetchone()
    assert day["requests"] == 1 and day["input_tokens"] == 1100 and day["output_tokens"] == 120
    # Same message with the same saved filters again: served from the de-duplication cache.
    hybrid.chat("start over")
    hybrid.chat("a calm 3 bhk home for my parents in Chintalaagraharam")
    assert len(hybrid.ai.calls) == 1


def test_budget_exhausted_means_no_paid_call(hybrid, monkeypatch):
    monkeypatch.setenv("LEIA_AI_DAILY_BUDGET_INR", "0")
    hybrid.ai.behaviour = lambda **kwargs: pytest.fail("provider must not be called over budget")
    result = hybrid.chat("something cosy for a young family")
    assert not hybrid.ai.calls
    assert "haven’t changed your search" in result["reply"]


def test_unauthenticated_users_cannot_trigger_paid_requests(hybrid):
    hybrid.ai.behaviour = lambda **kwargs: pytest.fail("provider must not be called")
    response = hybrid.http.post("/chat", json={"message": "a calm home", "conversation_id": hybrid.conversation})
    assert response.status_code == 401
    assert not hybrid.ai.calls


def test_request_id_header_and_comparison_from_records(hybrid):
    response = hybrid.http.get("/health", headers={"X-Request-ID": "pilot-check-1234"})
    assert response.headers["X-Request-ID"] == "pilot-check-1234"
    assert len(hybrid.http.get("/health").headers["X-Request-ID"]) == 32
    hybrid.chat("3 bhk in Chinthalagraharam sorted by price")
    comparison = hybrid.chat("compare listing 1 and 2")
    assert "Comparison from LEIA’s approved listing records" in comparison["reply"]
    assert "₹50,00,000" in comparison["reply"] and "₹68,00,000" in comparison["reply"]
    assert not hybrid.ai.calls


# --- Connection lifecycle of /chat (pooling work): one transaction, nothing held during Groq ----------
@pytest.fixture
def counted(hybrid, monkeypatch):
    from contextlib import contextmanager

    state = SimpleNamespace(blocks=0, open=0)

    @contextmanager
    def counting(*args, **kwargs):
        state.blocks += 1
        state.open += 1
        try:
            with hybrid.database() as connection:
                yield connection
        finally:
            state.open -= 1

    monkeypatch.setattr(hybrid.main, "get_connection", counting)
    return state


def test_deterministic_search_uses_one_transaction_and_reuses_it(hybrid, counted):
    counted.blocks = 0
    result = hybrid.chat("3 bhk in Chinthalagraharam sorted by price")
    assert hybrid.ids["mid"] in listing_ids(result)
    assert counted.blocks == 1  # lock + search + save on one connection (no nested connections)
    counted.blocks = 0
    empty = hybrid.chat("flats with power backup and parking")
    assert not empty["properties"]
    assert "drop the parking requirement (1 listing)" in empty["reply"]
    assert "drop the power-backup requirement (2 listings)" in empty["reply"]
    assert counted.blocks == 1  # suggestions are one query on the same connection
    assert not hybrid.ai.calls


def test_no_database_connection_is_held_while_waiting_for_groq(hybrid, counted):
    seen = []

    def answer(**kwargs):
        seen.append(counted.open)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                {"intent": "search", "bedrooms": 3, "location": "Chinthalagraharam"})))],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=100, completion_tokens_details=None))

    hybrid.ai.behaviour = answer
    result = hybrid.chat("a calm 3 bhk home for my parents in Chintalaagraharam")
    assert seen == [0]  # no /chat connection open during the provider call
    assert hybrid.ids["mid"] in listing_ids(result)
    assert counted.blocks == 2  # short read before Groq, one transaction after


def test_conversation_changed_during_groq_falls_back_without_using_stale_ai(hybrid):
    def answer(**kwargs):
        # Another message updates this conversation's filters while Groq is answering.
        with hybrid.database() as connection:
            connection.execute("UPDATE conversations SET filters = %s WHERE id = %s",
                               (json.dumps({"bedrooms": 2}), hybrid.conversation))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                {"intent": "search", "bedrooms": 3, "location": "Chinthalagraharam"})))],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=100, completion_tokens_details=None))

    hybrid.ai.behaviour = answer
    result = hybrid.chat("a calm 3 bhk home for my parents in Chintalaagraharam")
    assert len(hybrid.ai.calls) == 1
    assert "couldn’t interpret part" in result["reply"]  # ordinary search with what was understood
    assert result["filters"]["bedrooms"] == 3 and result["filters"]["location"] == "Chinthalagraharam"
    with hybrid.database() as connection:
        day = connection.execute("SELECT * FROM ai_usage_periods WHERE period LIKE 'day:%'").fetchone()
    assert day["requests"] == 1 and day["reserved_micro_inr"] == 0  # the real call is still accounted
