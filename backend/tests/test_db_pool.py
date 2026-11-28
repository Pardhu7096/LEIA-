"""Connection pool behaviour against a real local PostgreSQL (database ``leia_pool_test`` only).

Covers: reuse without leaks, rollback and cleanup after failures, transaction isolation, autocommit
reset, pool exhaustion (HTTP 503) and recovery, database restarts (killed connections), concurrent
AI budget reservations and rate limits through the pool, per-request metrics, and the
LEIA_DB_POOL_ENABLED=false rollback switch.
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database  # noqa: E402
import db_metrics  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
ALLOWED = {"leia_pool_test"}


@pytest.fixture(scope="module")
def url():
    value = os.getenv("TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL to the disposable leia_pool_test database.")
    if (urlparse(value).path or "/").lstrip("/") not in ALLOWED:
        pytest.skip("test_db_pool runs only against leia_pool_test.")
    with psycopg.connect(value, autocommit=True) as connection:
        for name in ("006_request_limits.sql", "013_ai_usage_ledger.sql"):
            table = "request_limits" if name.startswith("006") else "ai_usage_periods"
            if connection.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None:
                connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
        connection.execute("CREATE TABLE IF NOT EXISTS pool_probe (id INT PRIMARY KEY, note TEXT)")
    return value


@pytest.fixture
def pool(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LEIA_DB_POOL_ENABLED", "true")
    monkeypatch.setenv("LEIA_DB_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("LEIA_DB_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("LEIA_DB_POOL_TIMEOUT", "2")
    database.close_pool()
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("TRUNCATE pool_probe, request_limits, ai_usage_periods")
    yield database.get_pool()
    database.close_pool()


def checked_out_after_settling(timeout=3.0):
    """Connections still lent out once background growth has finished (a leak never reaches 0)."""
    deadline = time.monotonic() + timeout
    while True:
        stats = database.pool_stats()
        out = stats["pool_size"] - stats["pool_available"]
        if out == 0 or time.monotonic() > deadline:
            return out, stats
        time.sleep(0.05)


def backend_connections(url):
    with psycopg.connect(url, autocommit=True) as connection:
        return connection.execute(
            """SELECT count(*) FROM pg_stat_activity
               WHERE datname = current_database() AND pid <> pg_backend_pid()""").fetchone()[0]


def test_connections_are_reused_and_never_leak(pool, url):
    def work(i):
        with database.get_connection() as connection:
            return connection.execute("SELECT %s::int AS n, pg_backend_pid() AS pid", (i,)).fetchone()

    with ThreadPoolExecutor(16) as executor:
        rows = list(executor.map(work, range(200)))
    assert [row["n"] for row in rows] == list(range(200))
    assert len({row["pid"] for row in rows}) <= 4          # 200 uses, at most max_size connections
    out, stats = checked_out_after_settling()
    assert out == 0                                          # everything returned
    assert stats.get("requests_waiting", 0) == 0
    assert backend_connections(url) <= 4


def test_failed_transaction_rolls_back_and_connection_is_clean(pool):
    with pytest.raises(RuntimeError):
        with database.get_connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (1, 'must roll back')")
            raise RuntimeError("boom")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with database.get_connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (2, 'first')")
            connection.execute("INSERT INTO pool_probe VALUES (2, 'duplicate')")  # aborts the transaction
    for _ in range(8):  # every pooled connection is usable and idle afterwards
        with database.get_connection() as connection:
            assert connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            assert connection.autocommit is False
            assert connection.execute("SELECT count(*) AS n FROM pool_probe").fetchone()["n"] == 0


def test_successful_block_commits_and_autocommit_is_reset(pool):
    with database.get_connection() as connection:
        connection.execute("INSERT INTO pool_probe VALUES (3, 'committed')")
    with database.get_connection() as connection:
        connection.autocommit = True
        connection.execute("SELECT 1")
    for _ in range(6):
        with database.get_connection() as connection:
            assert connection.autocommit is False
            assert connection.execute("SELECT note FROM pool_probe WHERE id = 3").fetchone()["note"] == "committed"


def test_uncommitted_work_is_isolated_between_pooled_connections(pool):
    inside, release = threading.Event(), threading.Event()

    def writer():
        with database.get_connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (4, 'pending')")
            inside.set()
            release.wait(5)
            raise RuntimeError("roll back")

    thread = threading.Thread(target=lambda: pytest.raises(RuntimeError, writer))
    thread.start()
    assert inside.wait(5)
    with database.get_connection() as other:
        assert other.execute("SELECT count(*) AS n FROM pool_probe WHERE id = 4").fetchone()["n"] == 0
    release.set()
    thread.join(5)
    with database.get_connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM pool_probe WHERE id = 4").fetchone()["n"] == 0


def test_pool_exhaustion_returns_503_and_recovers(pool, monkeypatch):
    import main

    monkeypatch.setenv("LEIA_DB_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("LEIA_DB_POOL_TIMEOUT", "1")
    database.close_pool()
    database.get_pool().wait(5)
    before = db_metrics.totals["exhausted"]
    held, release = threading.Event(), threading.Event()

    def hold():
        with database.get_connection():
            held.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(5)
    started = time.perf_counter()
    with pytest.raises(database.PoolTimeout):
        with database.get_connection():
            pass
    assert 0.8 <= time.perf_counter() - started < 3       # bounded wait, no hang
    http = TestClient(main.app)
    busy = http.get("/properties")                          # needs a connection: none free
    assert busy.status_code == 503 and busy.headers["retry-after"] == "2"
    assert "busy" in busy.json()["detail"]
    assert db_metrics.totals["exhausted"] >= before + 2
    release.set()
    thread.join(5)
    with database.get_connection() as connection:           # recovered
        assert connection.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_database_restart_killed_connections_are_replaced(pool, url, monkeypatch):
    monkeypatch.setenv("LEIA_DB_POOL_CHECK_IDLE", "0")      # ping before every reuse
    database.close_pool()
    database.get_pool().wait(5)
    with database.get_connection() as connection:
        old_pid = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    with psycopg.connect(url, autocommit=True) as admin:    # simulate a restart / idle kill
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE datname = current_database() AND pid <> pg_backend_pid()")
    with database.get_connection() as connection:           # checked, discarded, replaced: no error
        assert connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"] != old_pid


def test_unchecked_stale_connection_fails_once_then_recovers(pool, url, monkeypatch):
    monkeypatch.setenv("LEIA_DB_POOL_CHECK_IDLE", "3600")   # never ping: worst case
    monkeypatch.setenv("LEIA_DB_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("LEIA_DB_POOL_MAX_SIZE", "1")
    database.close_pool()
    database.get_pool().wait(5)
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE datname = current_database() AND pid <> pg_backend_pid()")
    with pytest.raises(psycopg.OperationalError):
        with database.get_connection() as connection:
            connection.execute("SELECT 1")
    with database.get_connection() as connection:           # broken one was discarded
        assert connection.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_concurrent_ai_budget_reservations_never_exceed_the_cap(pool, monkeypatch):
    import ai_guard

    monkeypatch.setattr(ai_guard, "get_connection", database.get_connection)
    monkeypatch.setenv("LEIA_AI_DAILY_BUDGET_INR", "0.10")
    monkeypatch.setenv("LEIA_AI_MONTHLY_BUDGET_INR", "500")
    settings = ai_guard.load_settings()
    amount = 7_000  # micro-INR: at most 14 fit in 0.10 INR

    def attempt(_):
        try:
            ai_guard.reserve(settings, amount)
            return True
        except ai_guard.AIUnavailable as error:
            assert error.reason == "budget"
            return False

    with ThreadPoolExecutor(24) as executor:                 # 24 threads, 4 pooled connections
        results = list(executor.map(attempt, range(60)))
    assert sum(results) == 14
    with database.get_connection() as connection:
        day = connection.execute(
            "SELECT reserved_micro_inr FROM ai_usage_periods WHERE period LIKE 'day:%'").fetchone()
    assert day["reserved_micro_inr"] == 14 * amount <= 100_000
    assert checked_out_after_settling()[0] == 0


def test_rate_limits_through_the_pool_are_exact_and_multi_bucket(pool, monkeypatch):
    import rate_limits

    monkeypatch.setattr(rate_limits, "get_connection", database.get_connection)
    identity = f"pool-{time.time_ns()}"

    def hit(_):
        try:
            rate_limits.consume("pool-minute", identity, 5, 60, ("pool-day", identity, 7, 86400))
            return "ok"
        except rate_limits.LimitReached as error:
            return error.bucket

    with ThreadPoolExecutor(12) as executor:
        outcomes = list(executor.map(hit, range(30)))
    assert outcomes.count("ok") == 5
    assert set(outcomes) == {"ok", "pool-minute"}
    with pytest.raises(rate_limits.LimitReached) as error:
        rate_limits.consume("pool-minute2", identity, 50, 60, ("pool-day", identity, 7, 86400))
        rate_limits.consume("pool-minute2", identity, 50, 60, ("pool-day", identity, 7, 86400))
        rate_limits.consume("pool-minute2", identity, 50, 60, ("pool-day", identity, 7, 86400))
    assert error.value.bucket == "pool-day" and error.value.status_code == 429


def test_per_request_metrics_count_connections_and_queries(pool):
    stats, token = db_metrics.start_request()
    try:
        with database.get_connection() as connection:
            connection.execute("SELECT 1")
            with connection.cursor() as cursor:
                cursor.execute("SELECT 2")
    finally:
        db_metrics.end_request(token)
    assert stats.connections == 1 and stats.queries == 2 and stats.new_connections == 0
    assert stats.acquire_ms >= 0 and stats.query_ms > 0


def test_pool_can_be_switched_off_for_rollback(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LEIA_DB_POOL_ENABLED", "false")
    database.close_pool()
    stats, token = db_metrics.start_request()
    try:
        with database.get_connection() as connection:
            assert connection.execute("SELECT 1 AS one").fetchone()["one"] == 1
    finally:
        db_metrics.end_request(token)
    assert stats.new_connections == 1
    assert database.pool_stats() == {}
