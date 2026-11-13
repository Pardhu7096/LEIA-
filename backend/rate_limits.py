"""Shared fixed-window request limits, enforced before expensive operations."""
import hashlib
import hmac
import math
import os

from fastapi import HTTPException, Request
from psycopg import Error

from database import get_connection


def consume(bucket: str, identity: str, limit: int, seconds: int):
    # Do not store raw addresses or account identifiers in quota records.
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Request limits are unavailable.")
    key = hmac.new(secret.encode(), f"{bucket}:{identity}".encode(), hashlib.sha256).hexdigest()
    try:
        with get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '3s'")
            cursor.execute("""
                INSERT INTO public.request_limits AS current (key, count, expires_at)
                VALUES (%s, 1, clock_timestamp() + %s * interval '1 second')
                ON CONFLICT (key) DO UPDATE SET
                    count = CASE WHEN current.expires_at <= clock_timestamp()
                                 THEN 1 ELSE current.count + 1 END,
                    expires_at = CASE WHEN current.expires_at <= clock_timestamp()
                                 THEN EXCLUDED.expires_at ELSE current.expires_at END
                WHERE current.expires_at <= clock_timestamp() OR current.count < %s
                RETURNING count;
            """, (key, seconds, limit))
            allowed = cursor.fetchone() is not None
            wait = 1
            if not allowed:
                cursor.execute("""SELECT EXTRACT(EPOCH FROM expires_at - clock_timestamp()) AS wait
                                  FROM public.request_limits WHERE key = %s""", (key,))
                row = cursor.fetchone()
                wait = max(1, math.ceil(row["wait"])) if row else 1
            # Bounded cleanup using the expiry index, including on rejected requests.
            cursor.execute("""DELETE FROM public.request_limits WHERE key IN (
                SELECT key FROM public.request_limits WHERE expires_at < clock_timestamp()
                ORDER BY expires_at LIMIT 100)""")
    except (Error, OSError, KeyError):
        raise HTTPException(503, "Request limits are unavailable. Please try shortly.",
                            headers={"Retry-After": "5"}) from None
    if not allowed:
        raise HTTPException(429, "Request limit reached. Please try later.",
                            headers={"Retry-After": str(wait)})


def login_limit(request: Request):
    consume("login", request.client.host if request.client else "unknown", 10, 60)


def registration_limit(request: Request):
    consume("register", request.client.host if request.client else "unknown", 5, 3600)


def limit_chat(user):
    consume("chat-minute", str(user["id"]), 10, 60)
    consume("chat-day", str(user["id"]), 100, 86400)
    return user


def limit_maps(user):
    consume("maps-minute", str(user["id"]), 10, 60)
    consume("maps-day", str(user["id"]), 100, 86400)
    return user
