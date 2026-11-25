"""Customer sessions for the LEIA Android app.

The existing /auth/* cookie endpoints (customer web) and /auth/staff/*
(Agent CRM) are untouched. These endpoints never read or set cookies: the
refresh token travels only in JSON bodies so the app can keep it in the
platform keystore (expo-secure-store).

Trust model
-----------
The refresh token itself (384 random bits, stored only as a hash) is the
credential. Nothing about the caller's headers is treated as proof of a trusted
app. Requests that carry browser markers (Origin / Sec-Fetch-Site) are refused
only as defense in depth, so a script running in a web page cannot use this
token-in-body flow even if it learns a customer's password.

Rotation, retries and reuse
---------------------------
Every refresh replaces the token. The client sends a random request_id,
generated per refresh attempt and kept until that attempt succeeds. The next
token is derived with HMAC from (session, presented token, request_id), so:

* the first request rotates: current hash -> previous hash, new current hash;
* a retry of the *same* attempt (same old token + same request_id, within
  RETRY_GRACE_SECONDS) re-derives and returns the identical new token without
  storing it in plaintext. This covers lost responses and Render cold starts;
* the old token presented with any other request_id, or after the grace
  window, is treated as reuse: the whole session is revoked.

Concurrent requests for the same session are serialized with SELECT ... FOR
UPDATE on the session row.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from auth import (
    DUMMY_HASH,
    JWT_SECRET,
    SESSION_SECONDS,
    LoginRequest,
    create_access_token,
    password_hasher,
    token_hash,
)
from database import get_connection
from rate_limits import login_limit

router = APIRouter(prefix="/auth/native", tags=["Native customer sessions"])

RETRY_GRACE_SECONDS = 10 * 60
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{43,128}$"
# The app sends a CSPRNG UUIDv4 (122 random bits, 36 chars). The minimum length
# rejects trivially short ids from custom clients; entropy itself cannot be measured.
REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]{32,64}$"
INVALID_SESSION = "Session expired or invalid. Please log in again."


class NativeTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


class NativeRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(pattern=TOKEN_PATTERN)
    request_id: str = Field(pattern=REQUEST_ID_PATTERN)


class NativeLogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(pattern=TOKEN_PATTERN)


def native_hash(token: str) -> str:
    # Separate namespace: a native token never matches a cookie session.
    return token_hash(f"native:{token}")


def request_hash(request_id: str) -> str:
    return token_hash(f"native-request:{request_id}")


def derive_next_token(session_id: UUID, presented_hash: str, request_id: str) -> str:
    message = f"leia-native-refresh-v1|{session_id}|{presented_hash}|{request_id}"
    digest = hmac.new(JWT_SECRET.encode(), message.encode(), hashlib.sha512).digest()
    return base64.urlsafe_b64encode(digest[:48]).decode().rstrip("=")


def reject_browser_requests(request: Request):
    """Defense in depth only; never treated as proof of a trusted client."""
    if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        raise HTTPException(
            status_code=403,
            detail="This sign-in method is only available in the LEIA app.",
        )


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def token_payload(user_id, refresh_token: str, expires_at: datetime) -> dict:
    return {
        **create_access_token(user_id),
        "refresh_token": refresh_token,
        "refresh_expires_at": utc(expires_at),
    }


@router.post(
    "/login",
    response_model=NativeTokenResponse,
    dependencies=[Depends(reject_browser_requests), Depends(login_limit)],
)
def native_login(payload: LoginRequest, response: Response):
    no_store(response)
    email = str(payload.email).lower()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, password_hash, role FROM users WHERE email = %s;",
                (email,),
            )
            user = cursor.fetchone()

    # Verify against a dummy hash for unknown emails to keep timing uniform.
    stored_hash = user["password_hash"] if user else DUMMY_HASH
    password_valid = password_hasher.verify(payload.password, stored_hash)
    if user is None or not password_valid:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    refresh_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auth_sessions
                    (user_id, refresh_token_hash, expires_at)
                VALUES (%s, %s, %s);
                """,
                (user["id"], native_hash(refresh_token), expires_at),
            )

    return token_payload(user["id"], refresh_token, expires_at)


@router.post(
    "/refresh",
    response_model=NativeTokenResponse,
    dependencies=[Depends(reject_browser_requests)],
)
def native_refresh(payload: NativeRefreshRequest, response: Response):
    no_store(response)
    presented = native_hash(payload.refresh_token)
    attempt = request_hash(payload.request_id)
    now = datetime.now(timezone.utc)
    result = None
    reuse_detected = False

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, expires_at, revoked_at, refresh_token_hash,
                       previous_refresh_token_hash, rotation_request_hash, rotated_at
                FROM auth_sessions
                WHERE refresh_token_hash = %s
                   OR previous_refresh_token_hash = %s
                FOR UPDATE;
                """,
                (presented, presented),
            )
            session = cursor.fetchone()

            if (
                session is not None
                and session["revoked_at"] is None
                and utc(session["expires_at"]) > now
            ):
                if hmac.compare_digest(session["refresh_token_hash"], presented):
                    next_token = derive_next_token(
                        session["id"], presented, payload.request_id
                    )
                    cursor.execute(
                        """
                        UPDATE auth_sessions
                        SET refresh_token_hash = %s,
                            previous_refresh_token_hash = %s,
                            rotation_request_hash = %s,
                            rotated_at = %s
                        WHERE id = %s;
                        """,
                        (native_hash(next_token), presented, attempt, now, session["id"]),
                    )
                    result = (session["user_id"], next_token, session["expires_at"])
                else:
                    # The presented token was already rotated away.
                    retry = (
                        session["rotation_request_hash"] is not None
                        and hmac.compare_digest(session["rotation_request_hash"], attempt)
                        and session["rotated_at"] is not None
                        and utc(session["rotated_at"])
                        > now - timedelta(seconds=RETRY_GRACE_SECONDS)
                    )
                    next_token = derive_next_token(
                        session["id"], presented, payload.request_id
                    ) if retry else None

                    if retry and hmac.compare_digest(
                        native_hash(next_token), session["refresh_token_hash"]
                    ):
                        # Same attempt retried (lost response / cold start).
                        result = (session["user_id"], next_token, session["expires_at"])
                    elif retry:
                        # Derivation no longer matches (for example JWT_SECRET
                        # changed). Fail closed without blaming the client.
                        result = None
                    else:
                        cursor.execute(
                            """
                            UPDATE auth_sessions
                            SET revoked_at = %s, revoke_reason = 'reuse_detected'
                            WHERE id = %s AND revoked_at IS NULL;
                            """,
                            (now, session["id"]),
                        )
                        reuse_detected = True
        # Leaving the block commits, so a reuse revocation is persisted
        # before the 401 below is raised.

    if result is None or reuse_detected:
        raise HTTPException(status_code=401, detail=INVALID_SESSION)

    user_id, next_token, expires_at = result
    return token_payload(user_id, next_token, expires_at)


@router.post("/logout", dependencies=[Depends(reject_browser_requests)])
def native_logout(payload: NativeLogoutRequest, response: Response):
    no_store(response)
    presented = native_hash(payload.refresh_token)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Either the current or the just-rotated token may end the session.
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP, revoke_reason = 'logout'
                WHERE (refresh_token_hash = %s OR previous_refresh_token_hash = %s)
                  AND revoked_at IS NULL;
                """,
                (presented, presented),
            )

    # Same answer whether or not a session matched.
    return {"message": "Logged out."}
