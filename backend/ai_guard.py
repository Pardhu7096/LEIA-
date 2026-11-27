"""Cost protection for paid AI calls (Groq).

Every paid call goes through ``guarded_completion``:
  1. kill switch and input-size limits (no call when exceeded)
  2. per-user and global request limits (shared DB fixed windows, see rate_limits.py)
  3. a concurrency-safe budget *reservation* against daily and monthly caps (row locks
     in ``ai_usage_periods``; the worst-case cost is reserved before the call)
  4. bounded retries with exponential backoff + jitter, provider timeout per attempt
  5. settlement with the provider-reported token usage (falls back to the reservation)
The budget fails closed: if the ledger is unavailable no paid call is made, and the
caller falls back to ordinary (deterministic) property search.

A provider budget alert is not a hard limit; this ledger is LEIA's own hard cap.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException

from database import get_connection
from rate_limits import consume

MICRO = 1_000_000


class AIUnavailable(Exception):
    """No paid AI answer: the caller must fall back. ``reason`` is safe to log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class AISettings:
    enabled: bool
    model: str
    timeout_s: float
    max_attempts: int
    backoff_base_s: float
    max_output_tokens: int
    max_message_chars: int
    max_input_tokens: int
    user_per_minute: int
    user_per_day: int
    global_per_minute: int
    global_per_day: int
    price_in_usd_per_m: float
    price_out_usd_per_m: float
    usd_to_inr: float
    monthly_budget_inr: float
    daily_budget_inr: float
    cache_ttl_s: int

    @property
    def is_reasoning_model(self) -> bool:
        return self.model.startswith("openai/gpt-oss")


def load_settings() -> AISettings:
    monthly = _env_float("LEIA_AI_MONTHLY_BUDGET_INR", 500.0)
    return AISettings(
        enabled=os.getenv("LEIA_AI_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        model=os.getenv("LEIA_AI_MODEL", "openai/gpt-oss-20b"),
        timeout_s=_env_float("LEIA_AI_TIMEOUT_SECONDS", 10.0),
        max_attempts=max(1, min(_env_int("LEIA_AI_MAX_ATTEMPTS", 2), 3)),
        backoff_base_s=_env_float("LEIA_AI_BACKOFF_SECONDS", 0.5),
        max_output_tokens=_env_int("LEIA_AI_MAX_OUTPUT_TOKENS", 600),
        max_message_chars=_env_int("LEIA_AI_MAX_MESSAGE_CHARS", 500),
        max_input_tokens=_env_int("LEIA_AI_MAX_INPUT_TOKENS", 3000),
        user_per_minute=_env_int("LEIA_AI_USER_PER_MINUTE", 5),
        user_per_day=_env_int("LEIA_AI_USER_PER_DAY", 40),
        global_per_minute=_env_int("LEIA_AI_GLOBAL_PER_MINUTE", 25),
        global_per_day=_env_int("LEIA_AI_GLOBAL_PER_DAY", 900),
        # Groq console models table (2026-11-26): gpt-oss-20b $0.075 / 1M input, $0.30 / 1M output.
        price_in_usd_per_m=_env_float("LEIA_AI_PRICE_INPUT_USD_PER_M", 0.075),
        price_out_usd_per_m=_env_float("LEIA_AI_PRICE_OUTPUT_USD_PER_M", 0.30),
        usd_to_inr=_env_float("LEIA_AI_USD_TO_INR", 90.0),  # unverified assumption; keep conservative
        monthly_budget_inr=monthly,
        daily_budget_inr=_env_float("LEIA_AI_DAILY_BUDGET_INR", round(monthly / 20, 2)),
        cache_ttl_s=_env_int("LEIA_AI_CACHE_SECONDS", 900),
    )


def estimate_input_tokens(messages: list[dict]) -> int:
    """Pessimistic token estimate (Telugu script counts ~1 token per character)."""
    total = 0
    for message in messages:
        text = str(message.get("content", ""))
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        total += ascii_chars // 3 + (len(text) - ascii_chars) + 8
    return total + 32


def cost_micro_inr(settings: AISettings, input_tokens: int, output_tokens: int) -> int:
    usd = (input_tokens * settings.price_in_usd_per_m + output_tokens * settings.price_out_usd_per_m) / 1_000_000
    return max(1, round(usd * settings.usd_to_inr * MICRO))


def _periods(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    return f"day:{now:%Y-%m-%d}", f"month:{now:%Y-%m}"


def reserve(settings: AISettings, amount: int) -> tuple[str, str]:
    """Atomically reserve ``amount`` against the daily and monthly caps (row-locked)."""
    day, month = _periods()
    caps = {day: round(settings.daily_budget_inr * MICRO), month: round(settings.monthly_budget_inr * MICRO)}
    try:
        with get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL statement_timeout = '3s'")
            cursor.execute(
                """INSERT INTO public.ai_usage_periods (period) VALUES (%s), (%s)
                   ON CONFLICT (period) DO NOTHING""", (day, month))
            for period in (day, month):  # fixed order: no deadlocks between concurrent requests
                cursor.execute(
                    """UPDATE public.ai_usage_periods
                       SET reserved_micro_inr = reserved_micro_inr + %s, updated_at = clock_timestamp()
                       WHERE period = %s AND spent_micro_inr + reserved_micro_inr + %s <= %s
                       RETURNING period""", (amount, period, amount, caps[period]))
                if cursor.fetchone() is None:
                    connection.rollback()
                    raise AIUnavailable("budget")
    except AIUnavailable:
        raise
    except Exception:  # noqa: BLE001 - missing table, DB down, lock timeout: fail closed
        raise AIUnavailable("ledger_unavailable") from None
    return day, month


def settle(periods: tuple[str, str], reserved: int, spent: int, input_tokens: int, output_tokens: int,
           requests: int = 1) -> None:
    try:
        with get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '3s'")
            for period in periods:
                cursor.execute(
                    """UPDATE public.ai_usage_periods
                       SET reserved_micro_inr = greatest(reserved_micro_inr - %s, 0),
                           spent_micro_inr = spent_micro_inr + %s,
                           requests = requests + %s,
                           input_tokens = input_tokens + %s,
                           output_tokens = output_tokens + %s,
                           updated_at = clock_timestamp()
                       WHERE period = %s""",
                    (reserved, spent, requests, input_tokens, output_tokens, period))
    except Exception:  # noqa: BLE001 - reservation stays counted: errs on the safe side
        pass


def _limit(bucket: str, identity: str, limit: int, seconds: int, reason: str) -> None:
    try:
        consume(bucket, identity, limit, seconds)
    except HTTPException as error:
        raise AIUnavailable(reason if error.status_code == 429 else "limits_unavailable") from None


@dataclass
class Usage:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_micro_inr: int = 0
    cache_hit: bool = False


def _is_retryable(error: Exception) -> bool:
    name = type(error).__name__
    status = getattr(error, "status_code", None)
    return name in {"APITimeoutError", "APIConnectionError", "InternalServerError", "RateLimitError"} or (
        isinstance(status, int) and status >= 500)


def guarded_completion(user_id: str, create, messages: list[dict], usage: Usage, sleep=time.sleep):
    """Run one paid chat completion under all limits. Returns the completion or raises AIUnavailable."""
    settings = load_settings()
    if not settings.enabled:
        raise AIUnavailable("disabled")
    est_in = estimate_input_tokens(messages)
    if est_in > settings.max_input_tokens:
        raise AIUnavailable("input_too_large")
    _limit("ai-user-minute", user_id, settings.user_per_minute, 60, "user_rate")
    _limit("ai-user-day", user_id, settings.user_per_day, 86400, "user_rate")
    _limit("ai-global-minute", "all", settings.global_per_minute, 60, "global_rate")
    _limit("ai-global-day", "all", settings.global_per_day, 86400, "global_rate")

    kwargs = {
        "model": settings.model,
        "messages": messages,
        "temperature": 0,
        "max_completion_tokens": settings.max_output_tokens,
        "timeout": settings.timeout_s,
    }
    if settings.is_reasoning_model:
        kwargs["reasoning_effort"] = "low"
        kwargs["include_reasoning"] = False
    worst = cost_micro_inr(settings, est_in, settings.max_output_tokens)

    for attempt in range(settings.max_attempts):
        periods = reserve(settings, worst)
        usage.attempts += 1
        try:
            completion = create(**kwargs)
        except Exception as error:  # noqa: BLE001
            # A timed-out request may still be billed: keep the worst case as spent.
            billed = worst if type(error).__name__ == "APITimeoutError" else 0
            settle(periods, worst, billed, 0, 0)
            usage.cost_micro_inr += billed
            if attempt + 1 < settings.max_attempts and _is_retryable(error):
                retry_after = getattr(getattr(error, "response", None), "headers", {}) or {}
                wait = settings.backoff_base_s * (2 ** attempt) + random.uniform(0, settings.backoff_base_s)
                try:
                    wait = max(wait, float(retry_after.get("retry-after", 0)))
                except (TypeError, ValueError, AttributeError):
                    pass
                if wait > 5:  # never hold a customer request for long
                    raise AIUnavailable("provider_rate_limited") from None
                sleep(wait)
                continue
            raise AIUnavailable("provider_timeout" if type(error).__name__ == "APITimeoutError"
                                else "provider_error") from None
        reported = getattr(completion, "usage", None)
        in_tokens = getattr(reported, "prompt_tokens", None)
        out_tokens = getattr(reported, "completion_tokens", None)
        details = getattr(reported, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", 0) or 0
        if isinstance(in_tokens, int) and isinstance(out_tokens, int):
            # completion_tokens is treated as including reasoning tokens (billed as output).
            spent = cost_micro_inr(settings, in_tokens, max(out_tokens, reasoning))
        else:
            in_tokens, out_tokens, spent = est_in, settings.max_output_tokens, worst
        settle(periods, worst, spent, in_tokens, out_tokens)
        usage.input_tokens += in_tokens
        usage.output_tokens += out_tokens
        usage.reasoning_tokens += reasoning
        usage.cost_micro_inr += spent
        return completion
    raise AIUnavailable("provider_error")


class SingleFlightCache:
    """In-process TTL cache with in-flight de-duplication of identical AI requests.

    Concurrent identical requests wait for the first one instead of paying twice.
    Per instance only (Render staging runs one instance); documented in the report.
    """

    def __init__(self, max_entries: int = 2000):
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, str]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._max = max_entries

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def get_or_compute(self, key: str, ttl: int, compute, wait_s: float = 15.0) -> tuple[str, bool]:
        while True:
            with self._lock:
                hit = self._data.get(key)
                if hit and hit[0] > time.monotonic():
                    return hit[1], True
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    owner = True
                else:
                    owner = False
            if not owner:
                event.wait(wait_s)
                with self._lock:
                    hit = self._data.get(key)
                    if hit and hit[0] > time.monotonic():
                        return hit[1], True
                    if key in self._inflight:  # owner still running: do not pile on
                        raise AIUnavailable("duplicate_in_flight")
                continue  # owner failed: retry as owner
            try:
                value = compute()
                with self._lock:
                    if len(self._data) >= self._max:
                        for stale in sorted(self._data, key=lambda k: self._data[k][0])[: self._max // 10]:
                            self._data.pop(stale, None)
                    self._data[key] = (time.monotonic() + ttl, value)
                return value, False
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                event.set()

    def clear(self):
        with self._lock:
            self._data.clear()


extraction_cache = SingleFlightCache()
