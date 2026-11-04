import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, EmailStr, Field, field_validator
from pwdlib import PasswordHash

from database import get_connection

router = APIRouter(prefix="/auth", tags=["Authentication"])
password_hasher = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)

JWT_SECRET = os.getenv("JWT_SECRET", "")

if len(JWT_SECRET) < 32:
    raise RuntimeError("Set a strong JWT_SECRET in backend/.env")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_SECONDS = 15 * 60
SESSION_SECONDS = 7 * 24 * 60 * 60

COOKIE_NAME = "leia_refresh"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

ALLOWED_ORIGINS = {
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}

DUMMY_HASH = password_hasher.hash("unused-dummy-password")


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    name: str
    email: EmailStr
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def token_hash(token: str) -> str:
    # Random refresh tokens have high entropy; SHA-256 is suitable here.
    # User passwords continue to use Argon2.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(user_id: UUID) -> dict:
    now = datetime.now(timezone.utc)

    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_TOKEN_SECONDS),
            "iss": "leia-api",
            "aud": "leia-client",
            "token_type": "access",
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
    }


def set_refresh_cookie(
    response: Response,
    token: str,
    expires_at: datetime,
):
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    expires_at = expires_at.astimezone(timezone.utc)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        expires=expires_at,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/auth",
    )
    response.headers["Cache-Control"] = "no-store"


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Origin not allowed.")


def require_session_header(
    request: Request,
    x_leia_csrf: str | None = Header(default=None),
):
    check_origin(request)

    # A custom header prevents ordinary cross-site form submissions.
    # Keep CORS restricted to our explicit frontend origins.
    if x_leia_csrf != "1":
        raise HTTPException(
            status_code=403,
            detail="X-LEIA-CSRF header must be 1.",
        )


@router.post("/register", response_model=UserResponse, status_code=201)
def register(request: RegisterRequest):
    email = str(request.email).lower()
    hashed_password = password_hasher.hash(request.password)

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO users (name, email, password_hash)
                    VALUES (%s, %s, %s)
                    RETURNING id, name, email, created_at;
                    """,
                    (request.name, email, hashed_password),
                )
                user = cursor.fetchone()

    except UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    return user


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    response: Response,
    http_request: Request,
):
    check_origin(http_request)
    email = str(request.email).lower()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, password_hash FROM users WHERE email = %s;",
                (email,),
            )
            user = cursor.fetchone()

    stored_hash = user["password_hash"] if user else DUMMY_HASH
    password_valid = password_hasher.verify(request.password, stored_hash)

    if user is None or not password_valid:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    refresh_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)
    tokens = create_access_token(user["id"])

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Revoke the previous session in this browser, if present.
            previous_token = http_request.cookies.get(COOKIE_NAME)
            if previous_token:
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE refresh_token_hash = %s
                      AND revoked_at IS NULL;
                    """,
                    (token_hash(previous_token),),
                )

            cursor.execute(
                """
                INSERT INTO auth_sessions
                    (user_id, refresh_token_hash, expires_at)
                VALUES (%s, %s, %s);
                """,
                (user["id"], token_hash(refresh_token), expires_at),
            )

    set_refresh_cookie(response, refresh_token, expires_at)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    _: None = Depends(require_session_header),
):
    current_token = request.cookies.get(COOKIE_NAME)

    if not current_token:
        raise HTTPException(status_code=401, detail="Please log in again.")

    new_token = secrets.token_urlsafe(48)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Lock the session so simultaneous refreshes cannot both succeed.
            cursor.execute(
                """
                SELECT id, user_id, expires_at
                FROM auth_sessions
                WHERE refresh_token_hash = %s
                  AND revoked_at IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                FOR UPDATE;
                """,
                (token_hash(current_token),),
            )
            session = cursor.fetchone()

            if session is None:
                raise HTTPException(
                    status_code=401,
                    detail="Session expired or invalid. Please log in again.",
                )

            tokens = create_access_token(session["user_id"])

            cursor.execute(
                """
                UPDATE auth_sessions
                SET refresh_token_hash = %s
                WHERE id = %s;
                """,
                (token_hash(new_token), session["id"]),
            )

    # Rotation preserves the original seven-day session expiry.
    set_refresh_cookie(response, new_token, session["expires_at"])
    return tokens


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    _: None = Depends(require_session_header),
):
    current_token = request.cookies.get(COOKIE_NAME)

    if current_token:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE refresh_token_hash = %s
                      AND revoked_at IS NULL;
                    """,
                    (token_hash(current_token),),
                )

    response.delete_cookie(
        key=COOKIE_NAME,
        path="/auth",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Logged out."}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    unauthorized = HTTPException(
        status_code=401,
        detail="Invalid or expired access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer="leia-api",
            audience="leia-client",
            options={"require": ["sub", "iat", "exp", "iss", "aud", "token_type"]},
        )

        if payload["token_type"] != "access":
            raise unauthorized

        user_id = UUID(payload["sub"])

    except (InvalidTokenError, ValueError, TypeError, AttributeError):
        raise unauthorized

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, email, created_at
                FROM users
                WHERE id = %s;
                """,
                (user_id,),
            )
            user = cursor.fetchone()

    if user is None:
        raise unauthorized

    return user


@router.get("/me", response_model=UserResponse)
def me(user: dict = Depends(get_current_user)):
    return user
