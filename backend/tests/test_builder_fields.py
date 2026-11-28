"""Migration 014: builders, project facts, RERA verification, search, chat and Studio privacy.

Runs against an isolated local database (``leia_builder_test``) through the shared CRM fixture.
Groq is never called (deterministic searches only).
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import create_access_token  # noqa: E402
from test_crm import database, staff  # noqa: E402,F401  (shared isolated-database fixture)

BASE = {
    "title": "Builder Test Residency", "property_type": "flat", "price": 6400000, "bedrooms": 3,
    "area_sqft": "1250", "locality": "Madhurawada", "city": "Visakhapatnam", "facing": "north",
    "owner_name": "Owner", "owner_phone": "+91 90000 00001", "listing_consent": True,
}
FACTS = {
    "builder_name": "Raju  Constructions", "project_name": "Raju Greens", "rera_number": "P02400012345",
    "rera_source": "owner_reported", "construction_status": "under_construction",
    "possession_date": "2027-03-31", "floor_number": 4, "total_floors": 12, "lift": "yes",
    "gated_community": "no", "security": "guard_and_cctv", "amenities": ["pool", "gym", "gym"],
    "project_notes": "Internal: builder promised clubhouse", "project_source": "owner_reported",
}


@pytest.fixture
def app(database, monkeypatch):  # noqa: F811
    import auth
    import crm
    import main
    import property_media
    import rate_limits

    for module in (main, auth, crm, property_media, rate_limits):
        monkeypatch.setattr(module, "get_connection", database)
    monkeypatch.setattr(main, "limit_chat", lambda user: None)
    monkeypatch.setattr(main.client.chat.completions, "create",
                        lambda **kwargs: pytest.fail("Groq must not be called"))
    agent, other, admin, customer = (staff(database, role) for role in ("agent", "agent", "admin", "customer"))

    def headers(user):
        return {"Authorization": f"Bearer {create_access_token(user['id'])['access_token']}"}

    return SimpleNamespace(http=TestClient(main.app), db=database, agent=agent, other=other, admin=admin,
                           customer=customer, h=headers, main=main)


def create(app, user, **extra):
    response = app.http.post("/staff/properties", json={**BASE, **extra}, headers=app.h(user))
    assert response.status_code == 201, response.text
    return app.http.get(f"/staff/properties/{response.json()['id']}", headers=app.h(user)).json()


def save(app, user, record, **changes):
    body = {key: record[key] for key in record if key not in
            {"id", "created_by", "status", "availability", "created_at", "updated_at"}}
    return app.http.put(f"/staff/properties/{record['id']}", json={**body, **changes}, headers=app.h(user))


def publish(app, record):
    version = {"version": record["version"]}
    submitted = app.http.post(f"/staff/properties/{record['id']}/submit", json=version, headers=app.h(app.agent))
    assert submitted.status_code == 200, submitted.text
    approved = app.http.post(f"/staff/properties/{record['id']}/approve",
                             json={"version": submitted.json()["version"]}, headers=app.h(app.admin))
    assert approved.status_code == 200, approved.text


def test_new_fields_round_trip_and_builder_is_shared(app):
    record = create(app, app.agent, **FACTS)
    assert record["builder_name"] == "Raju Constructions"          # whitespace normalized
    assert record["amenities"] == ["gym", "pool"]                  # de-duplicated, sorted
    assert record["possession_date"] == "2027-03-31" and record["lift"] == "yes"
    assert record["gated_community"] == "no"                       # explicit no, not unknown
    second = create(app, app.other, **{**FACTS, "builder_name": "RAJU constructions."})
    assert second["builder_name"] == "Raju Constructions"          # same builder record reused
    with app.db() as connection:
        assert connection.execute("SELECT count(*) AS n FROM builders WHERE name_key = 'rajuconstructions'"
                                  ).fetchone()["n"] == 1


def test_concurrent_builder_creation_never_duplicates(app):
    names = ["Sri Sai Developers", "sri sai developers", "SRI-SAI developers", " Sri Sai  Developers "] * 5

    def make(name):
        return app.http.post("/staff/builders", json={"name": name}, headers=app.h(app.agent))

    with ThreadPoolExecutor(10) as executor:
        responses = list(executor.map(make, names))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses if r.status_code != 200]
    assert len({r.json()["id"] for r in responses}) == 1
    listed = app.http.get("/staff/builders", headers=app.h(app.other)).json()["builders"]
    import crm
    same = [b for b in listed if crm.builder_key(b["name"]) == "srisaidevelopers"]
    assert len(same) == 1                                           # one record, whichever spelling won
    assert set(listed[0]) == {"id", "name"}                         # no creator or internal data


def test_builder_rbac_and_validation(app):
    assert app.http.get("/staff/builders").status_code == 401
    assert app.http.get("/staff/builders", headers=app.h(app.customer)).status_code == 403
    assert app.http.post("/staff/builders", json={"name": "X Homes"}, headers=app.h(app.customer)).status_code == 403
    assert app.http.post("/staff/builders", json={"name": "!!!"}, headers=app.h(app.agent)).status_code == 422
    bad = app.http.post("/staff/properties", json={**BASE, "possession_date": "2027-01-31"}, headers=app.h(app.agent))
    assert bad.status_code == 422 and "under construction" in bad.text
    bad = app.http.post("/staff/properties", json={**BASE, "floor_number": 9, "total_floors": 5}, headers=app.h(app.agent))
    assert bad.status_code == 422 and "above the building" in bad.text
    bad = app.http.post("/staff/properties", json={**BASE, "rera_source": "owner_reported"}, headers=app.h(app.agent))
    assert bad.status_code == 422 and "RERA registration number" in bad.text
    bad = app.http.post("/staff/properties", json={**BASE, "rera_number": "bad number!"}, headers=app.h(app.agent))
    assert bad.status_code == 422
    bad = app.http.post("/staff/properties", json={**BASE, "amenities": ["helipad"]}, headers=app.h(app.agent))
    assert bad.status_code == 422


def test_only_admins_can_verify_rera(app):
    claimed = app.http.post("/staff/properties", json={**BASE, **FACTS, "rera_source": "document_checked"},
                            headers=app.h(app.agent))
    assert claimed.status_code == 403 and "Only an admin" in claimed.text
    record = create(app, app.agent, **FACTS)
    assert save(app, app.agent, record, rera_source="document_checked").status_code == 403
    # Admin verifies through the review endpoint (also works after the agent submits).
    submitted = app.http.post(f"/staff/properties/{record['id']}/submit", json={"version": record["version"]},
                              headers=app.h(app.agent)).json()
    assert app.http.post(f"/staff/properties/{record['id']}/rera-verification",
                         json={"version": submitted["version"], "verified": True},
                         headers=app.h(app.agent)).status_code == 403
    verified = app.http.post(f"/staff/properties/{record['id']}/rera-verification",
                             json={"version": submitted["version"], "verified": True}, headers=app.h(app.admin))
    assert verified.status_code == 200, verified.text
    assert verified.json()["rera_source"] == "document_checked" and verified.json()["rera_checked_on"]
    with app.db() as connection:
        row = connection.execute("SELECT rera_verified_by FROM properties WHERE id = %s", (record["id"],)).fetchone()
    assert row["rera_verified_by"] == app.admin["id"]


def test_agent_resave_keeps_verification_but_changing_the_number_drops_it(app):
    record = create(app, app.agent, **FACTS)
    admin_saved = save(app, app.admin, record, rera_source="document_checked")
    assert admin_saved.status_code == 200, admin_saved.text
    record = admin_saved.json()
    resaved = save(app, app.agent, record)                          # unchanged: verification kept
    assert resaved.status_code == 200 and resaved.json()["rera_source"] == "document_checked"
    record = resaved.json()
    changed = save(app, app.agent, record, rera_number="P02400099999")
    assert changed.status_code == 403                               # can't keep "verified" on a new number
    downgraded = save(app, app.agent, record, rera_number="P02400099999", rera_source="owner_reported")
    assert downgraded.status_code == 200 and downgraded.json()["rera_source"] == "owner_reported"


def test_old_app_payloads_keep_stored_project_facts(app):
    record = create(app, app.agent, **FACTS)
    legacy = {key: record[key] for key in (*BASE, "version") if key in record}
    legacy["area_sqft"] = str(record["area_sqft"])
    legacy["title"] = "Renamed by an old Agent CRM build"
    saved = app.http.put(f"/staff/properties/{record['id']}", json=legacy, headers=app.h(app.agent))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["title"] == legacy["title"]
    for key in ("builder_name", "project_name", "rera_number", "construction_status", "possession_date",
                "floor_number", "lift", "security", "amenities", "project_source"):
        assert body[key] == record[key], key                         # nothing wiped
    # An old payload creating a listing gets "unknown"/empty facts, never guessed ones.
    fresh = app.http.post("/staff/properties", json=BASE, headers=app.h(app.agent))
    assert fresh.status_code == 201
    details = app.http.get(f"/staff/properties/{fresh.json()['id']}", headers=app.h(app.agent)).json()
    assert details["lift"] == "unknown" and details["amenities"] == [] and details["builder_name"] is None


def test_publishing_search_chat_and_privacy(app):
    tag = uuid4().hex[:6]
    city = f"BuilderCity{tag}"
    verified = create(app, app.agent, **{**FACTS, "city": city, "title": f"Verified {tag}"})
    verified = save(app, app.admin, verified, rera_source="document_checked").json()
    publish(app, verified)
    unverified = create(app, app.agent, **{**FACTS, "city": city, "title": f"Unverified {tag}", "lift": "unknown",
                                           "builder_name": "Lakshmi Homes", "amenities": []})
    publish(app, unverified)
    draft = create(app, app.agent, **{**FACTS, "city": city, "title": f"Draft {tag}"})  # never published

    cards = app.http.get("/properties", params={"location": city}).json()["properties"]
    by_title = {card["title"]: card for card in cards}
    assert set(by_title) == {f"Verified {tag}", f"Unverified {tag}"}
    assert by_title[f"Verified {tag}"]["rera_number"] == "P02400012345"
    assert by_title[f"Verified {tag}"]["rera_verified"] is True
    assert by_title[f"Unverified {tag}"]["rera_number"] is None     # unverified RERA never public
    assert by_title[f"Unverified {tag}"]["rera_verified"] is False
    assert "project_notes" not in by_title[f"Verified {tag}"] and "owner_name" not in by_title[f"Verified {tag}"]

    customer_headers = app.h(app.customer)
    conversation = app.http.post("/conversations", headers=customer_headers).json()["id"]

    def chat(message, **extra):
        response = app.http.post("/chat", json={"message": message, "conversation_id": conversation, **extra},
                                 headers=customer_headers)
        assert response.status_code == 200, response.text
        return response.json()

    def ids(result):
        return {p["id"] for p in result["properties"]}

    with app.db() as connection:  # scope searches to this test's listings
        connection.execute("UPDATE properties SET locality = %s WHERE city = %s", (f"Loc{tag}", city))
    assert app.main.search_parser.parse("flats with lift").updates == {"lift": True, "property_type": "flat"}
    lift = app.main.filter_properties(app.main.SearchFilters(location=city, lift=True))
    assert {p["title"] for p in lift} == {f"Verified {tag}"}       # unknown lift never matches
    rera = app.main.filter_properties(app.main.SearchFilters(location=city, rera_verified=True))
    assert {p["title"] for p in rera} == {f"Verified {tag}"}
    builder = app.main.filter_properties(app.main.SearchFilters(location=city, builder="raju"))
    assert {p["title"] for p in builder} == {f"Verified {tag}"}
    gym = app.main.filter_properties(app.main.SearchFilters(location=city, amenities=["gym", "pool"]))
    assert {p["title"] for p in gym} == {f"Verified {tag}"}
    by_date = app.main.filter_properties(app.main.SearchFilters(location=city, possession_by="2027-06-30"))
    assert {p["title"] for p in by_date} == {f"Verified {tag}", f"Unverified {tag}"}
    early = app.main.filter_properties(app.main.SearchFilters(location=city, possession_by="2026-06-30"))
    assert early == []
    assert draft["id"] not in {p["id"] for p in lift + rera + builder + gym + by_date}

    # Chat: deterministic search and selected-listing facts with sources; nothing invented.
    found = chat("3 bhk flats with lift and gym")
    assert found["filters"]["lift"] is True and found["filters"]["amenities"] == ["gym"]
    facts = chat("who is the builder, is there a lift and what is the RERA number?",
                 property_id=unverified["id"])["reply"]
    assert "Lakshmi Homes" in facts and "Lift: Not listed" in facts
    assert "Not verified by LEIA" in facts and "P02400012345" not in facts
    verified_facts = chat("RERA and possession?", property_id=verified["id"])["reply"]
    assert "P02400012345 (verified from documents by LEIA)" in verified_facts
    assert "expected possession 2027-03-31 (an estimate, not a guarantee)" in verified_facts

    # Digital Studio snapshot: verified RERA only, internal notes never included.
    import marketing_jobs
    with app.db() as connection, connection.cursor() as cursor:
        snap_verified = marketing_jobs.build_snapshot(cursor, verified["id"])["property"]
        snap_unverified = marketing_jobs.build_snapshot(cursor, unverified["id"])["property"]
    assert snap_verified["rera_number"] == "P02400012345" and snap_verified["rera_verified"] is True
    assert snap_unverified["rera_number"] is None and snap_unverified["rera_verified"] is False
    assert snap_verified["builder_name"] == "Raju Constructions"
    for private in ("project_notes", "rera_source", "rera_checked_on", "owner_name", "agent_notes"):
        assert private not in snap_verified


def test_ai_output_cannot_set_project_fact_filters(app):
    import json

    payload = json.dumps({"intent": "search", "bedrooms": 3, "lift": True, "rera_verified": True,
                          "amenities": ["pool"], "builder": "Invented Builders"})
    result = app.main.generate_chat_reply(app.main.ChatRequest(message="a calm home", filters=app.main.SearchFilters()),
                                          extract=lambda messages: payload)
    for key in ("lift", "rera_verified", "amenities", "builder"):
        assert result["filters"][key] is None, key
    assert result["filters"]["bedrooms"] == 3
