"""Structured, privacy-preserving logs for customer search (stdout -> Render logs).

Never logs message text, conversation contents, tokens, secrets or user identifiers:
only request IDs, routes, counts, latencies, token usage and estimated cost.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid

import db_metrics

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_VALID_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

logger = logging.getLogger("leia.search")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def install_request_ids(app) -> None:
    @app.middleware("http")
    async def request_id_middleware(request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        stats, stats_token = db_metrics.start_request()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            request_id_var.reset(token)
            db_metrics.end_request(stats_token)
            total_ms = (time.perf_counter() - stats.started) * 1000
            try:
                slow_ms = float(os.getenv("LEIA_SLOW_REQUEST_MS", "1500"))
            except ValueError:
                slow_ms = 1500.0
            if total_ms >= slow_ms or stats.exhausted:
                route = request.scope.get("route")
                logger.info(json.dumps({
                    "event": "slow_request", "request_id": request_id, "method": request.method,
                    "route": getattr(route, "path", "unmatched"), "status": status,
                    "latency_ms": round(total_ms, 1), **stats.as_dict()}))
        response.headers["X-Request-ID"] = request_id
        if _flag("LEIA_SERVER_TIMING", "false"):
            # Aggregate numbers only (no SQL, no identifiers): lets staging measure latency remotely.
            response.headers["Server-Timing"] = (
                f"total;dur={total_ms:.1f}, db-acquire;dur={stats.acquire_ms:.1f}, "
                f"db-query;dur={stats.query_ms:.1f};desc=\"{stats.queries} queries\", "
                f"db-conn;desc=\"{stats.connections} connections\", ai;dur={stats.ai_ms:.1f}")
        return response


class SearchEvent:
    """Collects one customer search's metrics and emits a single JSON log line."""

    def __init__(self, kind: str = "chat_search"):
        self.started = time.perf_counter()
        self.data: dict = {
            "event": kind,
            "request_id": request_id_var.get(),
            "route": None,           # deterministic | ai | fallback | blocked | clarify | listing | compare
            "language": None,
            "deterministic_searches": 0,
            "groq_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "estimated_cost_inr": 0.0,
            "cache": None,           # hit | miss | None
            "results": None,
            "empty_result": False,
            "fallback_reason": None,  # budget | user_rate | global_rate | provider_* | invalid_ai_output | disabled ...
            "db_error": False,
            "unsupported": [],
        }

    def set(self, **values) -> None:
        self.data.update(values)

    def add_usage(self, usage) -> None:
        self.data["groq_requests"] += usage.attempts
        self.data["input_tokens"] += usage.input_tokens
        self.data["output_tokens"] += usage.output_tokens
        self.data["reasoning_tokens"] += usage.reasoning_tokens
        self.data["estimated_cost_inr"] = round(self.data["estimated_cost_inr"] + usage.cost_micro_inr / 1_000_000, 6)

    def emit(self) -> dict:
        self.data["latency_ms"] = round((time.perf_counter() - self.started) * 1000, 1)
        stats = db_metrics.current.get()
        if stats is not None:  # whole request so far, including auth and rate limits
            self.data["request_latency_ms"] = round((time.perf_counter() - stats.started) * 1000, 1)
            self.data.update(stats.as_dict())
        if self.data["fallback_reason"] in {"budget"}:
            self.data["budget_limited"] = True
        if self.data["fallback_reason"] in {"user_rate", "global_rate", "provider_rate_limited"}:
            self.data["rate_limited"] = True
        logger.info(json.dumps(self.data, ensure_ascii=False, default=str))
        return self.data
