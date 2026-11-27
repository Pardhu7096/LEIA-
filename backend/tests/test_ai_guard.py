"""Cost-protection tests for paid AI calls (mocked provider, isolated local database)."""
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_guard  # noqa: E402
import rate_limits  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
ALLOWED_DATABASES = {"leia_ai_test"}


@pytest.fixture
def ledger(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated disposable database.")

    def connect():
        return psycopg.connect(url, row_factory=dict_row)

    with connect() as connection:
        if connection.execute("SELECT current_database() AS d").fetchone()["d"] not in ALLOWED_DATABASES:
            pytest.fail("Use the explicitly allowed disposable database leia_ai_test.")
        connection.execute("DROP TABLE IF EXISTS public.ai_usage_periods, public.request_limits")
        connection.execute((MIGRATIONS / "006_request_limits.sql").read_text())
        connection.execute((MIGRATIONS / "013_ai_usage_ledger.sql").read_text())
    monkeypatch.setattr(ai_guard, "get_connection", connect)
    monkeypatch.setattr(rate_limits, "get_connection", connect)
    for name, value in {
        "LEIA_AI_MONTHLY_BUDGET_INR": "500", "LEIA_AI_DAILY_BUDGET_INR": "25",
        "LEIA_AI_USER_PER_MINUTE": "1000", "LEIA_AI_USER_PER_DAY": "1000",
        "LEIA_AI_GLOBAL_PER_MINUTE": "1000", "LEIA_AI_GLOBAL_PER_DAY": "1000",
        "LEIA_AI_BACKOFF_SECONDS": "0",
    }.items():
        monkeypatch.setenv(name, value)
    ai_guard.extraction_cache.clear()
    return connect


def completion(content='{"intent":"search"}', prompt=1200, out=150, reasoning=60):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=out,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning)),
    )


MESSAGES = [{"role": "system", "content": "x" * 3000}, {"role": "user", "content": "3 bhk somewhere nice"}]


def periods(connect):
    with connect() as connection:
        return {r["period"].split(":")[0]: r for r in connection.execute("SELECT * FROM ai_usage_periods")}


def test_actual_provider_usage_is_recorded_and_reservation_released(ledger):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return completion()

    usage = ai_guard.Usage()
    ai_guard.guarded_completion("user-1", create, MESSAGES, usage)
    settings = ai_guard.load_settings()
    expected = ai_guard.cost_micro_inr(settings, 1200, 150)
    rows = periods(ledger)
    assert rows["day"]["reserved_micro_inr"] == 0 and rows["month"]["reserved_micro_inr"] == 0
    assert rows["day"]["spent_micro_inr"] == expected == rows["month"]["spent_micro_inr"]
    assert rows["day"]["input_tokens"] == 1200 and rows["day"]["output_tokens"] == 150
    assert usage.attempts == 1 and usage.reasoning_tokens == 60
    kwargs = calls[0]
    assert kwargs["model"] == "openai/gpt-oss-20b"
    assert kwargs["max_completion_tokens"] == 600 and kwargs["timeout"] == 10.0
    assert kwargs["reasoning_effort"] == "low" and kwargs["include_reasoning"] is False


def test_budget_cap_blocks_before_calling_the_provider(ledger, monkeypatch):
    monkeypatch.setenv("LEIA_AI_DAILY_BUDGET_INR", "0.0001")  # 100 micro-rupees
    called = []
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("user-1", lambda **k: called.append(1), MESSAGES, ai_guard.Usage())
    assert error.value.reason == "budget" and not called


def test_concurrent_requests_never_exceed_the_cap(ledger, monkeypatch):
    settings = ai_guard.load_settings()
    worst = ai_guard.cost_micro_inr(settings, ai_guard.estimate_input_tokens(MESSAGES), settings.max_output_tokens)
    monkeypatch.setenv("LEIA_AI_DAILY_BUDGET_INR", str(worst * 5 / 1_000_000))  # room for exactly 5 worst cases
    gate = threading.Barrier(12)
    outcomes = []

    def create(**kwargs):
        return completion(prompt=ai_guard.estimate_input_tokens(MESSAGES), out=settings.max_output_tokens)

    def worker(i):
        gate.wait()
        try:
            ai_guard.guarded_completion(f"user-{i}", create, MESSAGES, ai_guard.Usage())
            outcomes.append("ok")
        except ai_guard.AIUnavailable as error:
            outcomes.append(error.reason)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    day = periods(ledger)["day"]
    assert outcomes.count("ok") <= 5 and outcomes.count("ok") + outcomes.count("budget") == 12
    assert day["spent_micro_inr"] + day["reserved_micro_inr"] <= worst * 5
    assert day["reserved_micro_inr"] == 0


def test_missing_ledger_fails_closed(ledger):
    with ledger() as connection:
        connection.execute("DROP TABLE ai_usage_periods")
    called = []
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("user-1", lambda **k: called.append(1), MESSAGES, ai_guard.Usage())
    assert error.value.reason == "ledger_unavailable" and not called


def test_kill_switch_and_input_limit(ledger, monkeypatch):
    monkeypatch.setenv("LEIA_AI_ENABLED", "false")
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("u", lambda **k: completion(), MESSAGES, ai_guard.Usage())
    assert error.value.reason == "disabled"
    monkeypatch.setenv("LEIA_AI_ENABLED", "true")
    monkeypatch.setenv("LEIA_AI_MAX_INPUT_TOKENS", "100")
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("u", lambda **k: completion(), MESSAGES, ai_guard.Usage())
    assert error.value.reason == "input_too_large"


def test_per_user_and_global_limits(ledger, monkeypatch):
    monkeypatch.setenv("LEIA_AI_USER_PER_MINUTE", "2")
    for _ in range(2):
        ai_guard.guarded_completion("busy", lambda **k: completion(), MESSAGES, ai_guard.Usage())
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("busy", lambda **k: completion(), MESSAGES, ai_guard.Usage())
    assert error.value.reason == "user_rate"
    monkeypatch.setenv("LEIA_AI_GLOBAL_PER_MINUTE", "3")
    ai_guard.guarded_completion("other", lambda **k: completion(), MESSAGES, ai_guard.Usage())
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("third", lambda **k: completion(), MESSAGES, ai_guard.Usage())
    assert error.value.reason == "global_rate"


class APITimeoutError(Exception):
    pass


class InternalServerError(Exception):
    status_code = 503


class BadRequestError(Exception):
    status_code = 400


def test_bounded_retries_with_backoff(ledger):
    attempts, sleeps = [], []

    def flaky(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise InternalServerError()
        return completion()

    usage = ai_guard.Usage()
    ai_guard.guarded_completion("u", flaky, MESSAGES, usage, sleep=sleeps.append)
    assert len(attempts) == 2 and usage.attempts == 2 and len(sleeps) == 1

    def always_down(**kwargs):
        raise InternalServerError()

    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("u", always_down, MESSAGES, ai_guard.Usage(), sleep=lambda s: None)
    assert error.value.reason == "provider_error"

    calls = []

    def bad_request(**kwargs):
        calls.append(1)
        raise BadRequestError()

    with pytest.raises(ai_guard.AIUnavailable):
        ai_guard.guarded_completion("u", bad_request, MESSAGES, ai_guard.Usage(), sleep=lambda s: None)
    assert len(calls) == 1  # non-retryable errors are not retried
    assert periods(ledger)["day"]["reserved_micro_inr"] == 0


def test_timeouts_are_charged_conservatively(ledger, monkeypatch):
    monkeypatch.setenv("LEIA_AI_MAX_ATTEMPTS", "1")

    def slow(**kwargs):
        raise APITimeoutError()

    usage = ai_guard.Usage()
    with pytest.raises(ai_guard.AIUnavailable) as error:
        ai_guard.guarded_completion("u", slow, MESSAGES, usage)
    assert error.value.reason == "provider_timeout"
    assert usage.cost_micro_inr > 0 and periods(ledger)["day"]["spent_micro_inr"] == usage.cost_micro_inr


def test_single_flight_deduplicates_identical_concurrent_requests():
    cache = ai_guard.SingleFlightCache()
    calls, started = [], threading.Event()
    release = threading.Event()

    def compute():
        calls.append(1)
        started.set()
        release.wait(5)
        return "content"

    results = []
    first = threading.Thread(target=lambda: results.append(cache.get_or_compute("k", 60, compute)))
    first.start()
    started.wait(5)
    waiters = [threading.Thread(target=lambda: results.append(cache.get_or_compute("k", 60, compute)))
               for _ in range(5)]
    [t.start() for t in waiters]
    release.set()
    first.join()
    [t.join() for t in waiters]
    assert len(calls) == 1
    assert sorted(hit for _, hit in results) == [False] + [True] * 5
    assert cache.get_or_compute("k", 60, compute) == ("content", True)
