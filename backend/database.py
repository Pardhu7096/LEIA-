"""PostgreSQL access: a per-process connection pool (psycopg_pool) behind ``get_connection()``.

``with get_connection() as connection:`` keeps its old meaning: one transaction that commits when the
block succeeds and rolls back when it raises. The connection then goes back to the pool (reset to
idle and non-autocommit) instead of being closed, so requests no longer pay a new TCP + TLS +
SCRAM handshake for every query.

Rules for callers:
- Keep blocks short. Never call Groq or another network service while holding a connection.
- Don't nest ``get_connection()`` blocks in one request: pass the open cursor/connection instead
  (nesting needs two pool slots and can exhaust a small pool under load).

Environment (all optional):
  LEIA_DB_POOL_ENABLED        true; "false" restores one new connection per call (rollback switch)
  LEIA_DB_POOL_MIN_SIZE       1
  LEIA_DB_POOL_MAX_SIZE       5    per backend process (total = workers x max)
  LEIA_DB_POOL_TIMEOUT        5    seconds to wait for a free connection before HTTP 503
  LEIA_DB_POOL_MAX_IDLE       300  seconds before an idle connection above min_size is closed
  LEIA_DB_POOL_MAX_LIFETIME   1800 seconds before a connection is replaced
  LEIA_DB_POOL_CHECK_IDLE     30   seconds idle after which a connection is pinged before reuse
  LEIA_DB_CONNECT_TIMEOUT     10   seconds for a new connection
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

import db_metrics

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("leia.db")

try:  # psycopg_pool is a separate package of the psycopg project (requirements.txt).
    from psycopg_pool import ConnectionPool, PoolTimeout
except ImportError:  # pragma: no cover - only without the dependency installed
    ConnectionPool = None

    class PoolTimeout(psycopg.OperationalError):
        pass

_pool: "ConnectionPool | None" = None
_pool_url: str | None = None
_pool_lock = threading.Lock()


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        value = default
    return max(low, min(value, high))


def pool_enabled() -> bool:
    return (ConnectionPool is not None
            and os.getenv("LEIA_DB_POOL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"})


def pool_settings() -> dict:
    min_size = _env_int("LEIA_DB_POOL_MIN_SIZE", 1, 0, 20)
    max_size = max(min_size, _env_int("LEIA_DB_POOL_MAX_SIZE", 5, 1, 40))
    return {
        "min_size": min_size,
        "max_size": max_size,
        "timeout": float(_env_int("LEIA_DB_POOL_TIMEOUT", 5, 1, 60)),
        "max_idle": float(_env_int("LEIA_DB_POOL_MAX_IDLE", 300, 30, 3600)),
        "max_lifetime": float(_env_int("LEIA_DB_POOL_MAX_LIFETIME", 1800, 60, 86400)),
        "check_idle": float(_env_int("LEIA_DB_POOL_CHECK_IDLE", 30, 0, 3600)),
        "connect_timeout": _env_int("LEIA_DB_CONNECT_TIMEOUT", 10, 1, 60),
    }


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return database_url


def _configure(connection: psycopg.Connection) -> None:
    connection.autocommit = False
    connection._leia_returned_at = time.monotonic()


def _reset(connection: psycopg.Connection) -> None:
    # Called when a connection comes back to the pool (after commit/rollback): restore defaults.
    connection.autocommit = False
    connection._leia_returned_at = time.monotonic()


def _make_check(check_idle: float):
    def check(connection: psycopg.Connection) -> None:
        """Ping only connections idle for a while: catches restarts without a round trip per use."""
        idle = time.monotonic() - getattr(connection, "_leia_returned_at", 0.0)
        if idle >= check_idle:
            ConnectionPool.check_connection(connection)  # raises -> pool discards it, tries another
    return check


def get_pool() -> "ConnectionPool":
    """The process-wide pool, created on first use (and opened by the app lifespan)."""
    global _pool, _pool_url
    url = _database_url()
    if _pool is not None and _pool_url == url:
        return _pool
    with _pool_lock:
        if _pool is not None and _pool_url != url:  # DATABASE_URL changed (tests): start fresh
            _pool.close()
            _pool = None
        if _pool is None:
            s = pool_settings()
            _pool = ConnectionPool(
                url,
                min_size=s["min_size"], max_size=s["max_size"], timeout=s["timeout"],
                max_idle=s["max_idle"], max_lifetime=s["max_lifetime"],
                kwargs={"row_factory": dict_row, "connect_timeout": s["connect_timeout"],
                        "cursor_factory": db_metrics.TimedCursor},
                configure=_configure, reset=_reset, check=_make_check(s["check_idle"]),
                name="leia", open=False,
            )
            _pool.open(wait=False)  # never block startup on the database; connections fill in
            _pool_url = url
    return _pool


def open_pool() -> None:
    if pool_enabled() and os.getenv("DATABASE_URL"):
        get_pool()


def close_pool() -> None:
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None:
            _pool.close(timeout=5)
        _pool, _pool_url = None, None


def pool_stats() -> dict:
    return _pool.get_stats() if _pool is not None else {}


@contextmanager
def get_connection(autocommit: bool = False):
    """One short transaction (or, with ``autocommit=True``, single statements without BEGIN/COMMIT)."""
    if not pool_enabled():
        started = time.perf_counter()
        with psycopg.connect(_database_url(), row_factory=dict_row, connect_timeout=15,
                             cursor_factory=db_metrics.TimedCursor, autocommit=autocommit) as connection:
            db_metrics.record_acquire((time.perf_counter() - started) * 1000, new=True)
            yield connection
        return

    pool = get_pool()
    started = time.perf_counter()
    try:
        context = pool.connection()
        connection = context.__enter__()
    except PoolTimeout:
        db_metrics.record_exhausted()
        logger.warning('{"event":"db_pool_exhausted","waited_ms":%d,"stats":%s}',
                       (time.perf_counter() - started) * 1000, _safe_stats())
        raise
    db_metrics.record_acquire((time.perf_counter() - started) * 1000)
    try:
        if autocommit:
            connection.autocommit = True
        yield connection
    except BaseException as error:
        if not context.__exit__(type(error), error, error.__traceback__):
            raise
    else:
        context.__exit__(None, None, None)


def _safe_stats() -> str:
    keys = ("pool_min", "pool_max", "pool_size", "pool_available", "requests_waiting", "requests_errors",
            "requests_num", "requests_queued", "connections_num", "connections_errors", "connections_lost")
    stats = pool_stats()
    return "{" + ",".join(f'"{k}":{int(stats[k])}' for k in keys if k in stats) + "}"
