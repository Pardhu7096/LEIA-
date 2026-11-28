"""Per-request database metrics: connection acquisition, query count and time, pool exhaustion.

Only numbers are recorded: never SQL text, parameters, customer data or credentials.
A middleware (observability.install_request_ids) starts a ``RequestDBStats`` for each request;
code running in FastAPI's threadpool sees the same object through the copied context.
"""
from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass, field

import psycopg


@dataclass
class RequestDBStats:
    connections: int = 0          # get_connection() blocks entered
    new_connections: int = 0      # brand-new connections (pool disabled)
    acquire_ms: float = 0.0       # waiting for / opening connections
    queries: int = 0
    query_ms: float = 0.0         # time inside execute() (includes network round trips)
    exhausted: int = 0            # pool timeouts
    ai_ms: float = 0.0            # Groq provider time (measured by ai_guard)
    started: float = field(default_factory=time.perf_counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def as_dict(self) -> dict:
        return {"db_connections": self.connections, "db_new_connections": self.new_connections,
                "db_acquire_ms": round(self.acquire_ms, 1), "db_queries": self.queries,
                "db_query_ms": round(self.query_ms, 1), "db_pool_exhausted": self.exhausted,
                "ai_provider_ms": round(self.ai_ms, 1)}


current: contextvars.ContextVar[RequestDBStats | None] = contextvars.ContextVar("leia_db_stats", default=None)

# Process-wide counters (for logs and tests); never reset by requests.
totals = {"exhausted": 0}
_totals_lock = threading.Lock()


def start_request() -> tuple[RequestDBStats, contextvars.Token]:
    stats = RequestDBStats()
    return stats, current.set(stats)


def end_request(token: contextvars.Token) -> None:
    current.reset(token)


def record_acquire(ms: float, new: bool = False) -> None:
    stats = current.get()
    if stats is not None:
        with stats._lock:
            stats.connections += 1
            stats.new_connections += int(new)
            stats.acquire_ms += ms


def record_exhausted() -> None:
    with _totals_lock:
        totals["exhausted"] += 1
    stats = current.get()
    if stats is not None:
        with stats._lock:
            stats.exhausted += 1


def record_ai(ms: float) -> None:
    stats = current.get()
    if stats is not None:
        with stats._lock:
            stats.ai_ms += ms


class TimedCursor(psycopg.Cursor):
    """Counts and times statements for the current request; behaviour is otherwise unchanged."""

    def execute(self, query, params=None, **kwargs):
        started = time.perf_counter()
        try:
            return super().execute(query, params, **kwargs)
        finally:
            stats = current.get()
            if stats is not None:
                with stats._lock:
                    stats.queries += 1
                    stats.query_ms += (time.perf_counter() - started) * 1000
