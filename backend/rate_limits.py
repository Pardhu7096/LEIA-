"""Shared fixed-window request limits, enforced before expensive operations."""
import hashlib
import hmac
import math
import os

from fastapi import HTTPException, Request
from psycopg import Error

from database import get_connection


class LimitReached(HTTPException):
    """HTTP 429 that also says which bucket was full (safe to log: bucket names only)."""

    def __init__(self, bucket: str, wait: int):
        super().__init__(429, "Request limit reached. Please try later.", headers={"Retry-After": str(wait)})
        self.bucket = bucket


_UPSERT = """
    b{i} AS (
        INSERT INTO public.request_limits AS current (key, count, expires_at)
        SELECT %(key{i})s, 1, clock_timestamp() + %(seconds{i})s * interval '1 second'
        {guard}
        ON CONFLICT (key) DO UPDATE SET
            count = CASE WHEN current.expires_at <= clock_timestamp()
                         THEN 1 ELSE current.count + 1 END,
            expires_at = CASE WHEN current.expires_at <= clock_timestamp()
                         THEN EXCLUDED.expires_at ELSE current.expires_at END
        WHERE current.expires_at <= clock_timestamp() OR current.count < %(limit{i})s
        RETURNING count
    )"""


def _consume_sql(n: int) -> str:
    """One statement for n buckets. Bucket i is only counted when bucket i-1 accepted the request
    (same as checking them one after another). Also deletes up to 100 expired rows, never one of
    the keys being counted (a statement can't modify the same row twice)."""
    keys = ", ".join(f"%(key{i})s" for i in range(n))
    ctes = [f"""cleanup AS (
        DELETE FROM public.request_limits WHERE key IN (
            SELECT key FROM public.request_limits
            WHERE expires_at < clock_timestamp() AND key NOT IN ({keys})
            ORDER BY expires_at LIMIT 100 FOR UPDATE SKIP LOCKED)
    )"""]
    for i in range(n):
        ctes.append(_UPSERT.replace("{i}", str(i)).replace(
            "{guard}", "" if i == 0 else f"WHERE EXISTS (SELECT 1 FROM b{i - 1})"))
    accepted = ", ".join(f"(SELECT count(*) FROM b{i}) AS ok{i}" for i in range(n))
    return "WITH " + ",".join(ctes) + f"\nSELECT {accepted}"


def consume(bucket: str, identity: str, limit: int, seconds: int, *more: tuple[str, str, int, int]):
    """Count one request against each (bucket, identity, limit, seconds), in order, in ONE statement.

    Stops at the first full bucket (earlier buckets stay counted, as with separate calls) and raises
    ``LimitReached`` (HTTP 429). Fails closed with 503 when the store is unavailable.
    """
    # Do not store raw addresses or account identifiers in quota records.
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Request limits are unavailable.")
    items = [(bucket, identity, limit, seconds), *more]
    params, keys = {}, []
    for i, (name, who, cap, window) in enumerate(items):
        key = hmac.new(secret.encode(), f"{name}:{who}".encode(), hashlib.sha256).hexdigest()
        keys.append(key)
        params.update({f"key{i}": key, f"seconds{i}": window, f"limit{i}": cap})
    rejected = None
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                # Pipeline: BEGIN, the timeout and the counting statement travel in one round trip.
                with connection.pipeline():
                    cursor.execute("SET LOCAL statement_timeout = '3s'")
                    cursor.execute(_consume_sql(len(items)), params)
                    row = cursor.fetchone()
                full = next((i for i in range(len(items)) if not row[f"ok{i}"]), None)
                if full is not None:
                    cursor.execute("""SELECT EXTRACT(EPOCH FROM expires_at - clock_timestamp()) AS wait
                                      FROM public.request_limits WHERE key = %s""", (keys[full],))
                    wait_row = cursor.fetchone()
                    rejected = (items[full][0], max(1, math.ceil(wait_row["wait"])) if wait_row else 1)
    except (Error, OSError, KeyError):
        raise HTTPException(503, "Request limits are unavailable. Please try shortly.",
                            headers={"Retry-After": "5"}) from None
    if rejected:
        raise LimitReached(*rejected)


def login_limit(request: Request):
    consume("login", request.client.host if request.client else "unknown", 10, 60)


def registration_limit(request: Request):
    consume("register", request.client.host if request.client else "unknown", 5, 3600)


def limit_chat(user):
    consume("chat-minute", str(user["id"]), 10, 60, ("chat-day", str(user["id"]), 100, 86400))
    return user


def limit_maps(user):
    consume("maps-minute", str(user["id"]), 10, 60, ("maps-day", str(user["id"]), 100, 86400))
    return user
