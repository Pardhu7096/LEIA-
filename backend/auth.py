import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, EmailStr, Field, field_validator
from pwdlib import PasswordHash

from database import get_connection
from preflight import parse_origins
from rate_limits import login_limit, registration_limit


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
STAFF_COOKIE_NAME = "leia_staff_refresh"
STAFF_ROLES = {"agent", "admin"}
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

DEVELOPMENT_ORIGINS = {
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:8082",
    "http://127.0.0.1:8082",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}

# LEIA_ALLOWED_ORIGINS (comma-separated) replaces the local defaults, so a
# deployed server accepts only its own HTTPS web origins.
ALLOWED_ORIGINS = parse_origins(os.getenv("LEIA_ALLOWED_ORIGINS")) or DEVELOPMENT_ORIGINS

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
    role: Literal["customer", "agent", "admin"]
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def token_hash(token: str) -> str:
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


def session_token_hash(token: str, staff: bool) -> str:
    # A customer refresh token cannot be replayed through a staff cookie.
    return token_hash(f"staff:{token}" if staff else token)


def set_refresh_cookie(
    response: Response,
    token: str,
    expires_at: datetime,
    *,
    staff: bool,
):
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    expires_at = expires_at.astimezone(timezone.utc)

    response.set_cookie(
        key=STAFF_COOKIE_NAME if staff else COOKIE_NAME,
        value=token,
        expires=expires_at,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/auth/staff" if staff else "/auth",
    )

    response.headers["Cache-Control"] = "no-store"


def check_origin(request: Request):
    origin = request.headers.get("origin")

    if origin is not None and origin not in ALLOWED_ORIGINS:
        raise HTTPException(
            status_code=403,
            detail="Origin not allowed.",
        )


def require_session_header(
    request: Request,
    x_leia_csrf: str | None = Header(default=None),
):
    check_origin(request)

    if x_leia_csrf != "1":
        raise HTTPException(
            status_code=403,
            detail="X-LEIA-CSRF header must be 1.",
        )


@router.post("/register", response_model=UserResponse, status_code=201,
             dependencies=[Depends(registration_limit)])
def register(request: RegisterRequest):
    email = str(request.email).lower()
    hashed_password = password_hasher.hash(request.password)

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                # Public registration always creates a customer.
                cursor.execute(
                    """
                    INSERT INTO users (name, email, password_hash, role)
                    VALUES (%s, %s, %s, 'customer')
                    RETURNING id, name, email, role, created_at;
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


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_limit)])
def login(
    request: LoginRequest,
    response: Response,
    http_request: Request,
):
    return start_session(request, response, http_request, staff=False)


@router.post("/staff/login", response_model=TokenResponse, dependencies=[Depends(login_limit)])
def staff_login(request: LoginRequest, response: Response, http_request: Request):
    return start_session(request, response, http_request, staff=True)


def start_session(
    request: LoginRequest, response: Response, http_request: Request, *, staff: bool
):
    check_origin(http_request)
    email = str(request.email).lower()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, password_hash, role FROM users WHERE email = %s;",
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

    if staff and user["role"] not in STAFF_ROLES:
        raise HTTPException(
            status_code=403, detail="Agent or admin access is required."
        )

    refresh_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=SESSION_SECONDS
    )
    tokens = create_access_token(user["id"])

    with get_connection() as connection:
        with connection.cursor() as cursor:
            previous_token = http_request.cookies.get(
                STAFF_COOKIE_NAME if staff else COOKIE_NAME
            )

            if previous_token:
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE refresh_token_hash = %s
                      AND revoked_at IS NULL;
                    """,
                    (session_token_hash(previous_token, staff),),
                )

            cursor.execute(
                """
                INSERT INTO auth_sessions
                    (user_id, refresh_token_hash, expires_at)
                VALUES (%s, %s, %s);
                """,
                (user["id"], session_token_hash(refresh_token, staff), expires_at),
            )

    set_refresh_cookie(response, refresh_token, expires_at, staff=staff)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    _: None = Depends(require_session_header),
):
    return rotate_session(request, response, staff=False)


@router.post("/staff/refresh", response_model=TokenResponse)
def staff_refresh(
    request: Request, response: Response, _: None = Depends(require_session_header)
):
    return rotate_session(request, response, staff=True)


def rotate_session(request: Request, response: Response, *, staff: bool):
    current_token = request.cookies.get(STAFF_COOKIE_NAME if staff else COOKIE_NAME)

    if not current_token:
        raise HTTPException(
            status_code=401,
            detail="Please log in again.",
        )

    new_token = secrets.token_urlsafe(48)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, expires_at
                FROM auth_sessions
                WHERE refresh_token_hash = %s
                  AND revoked_at IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                FOR UPDATE;
                """,
                (session_token_hash(current_token, staff),),
            )

            session = cursor.fetchone()

            if session is None:
                raise HTTPException(
                    status_code=401,
                    detail="Session expired or invalid. Please log in again.",
                )

            if staff:
                cursor.execute(
                    "SELECT role FROM users WHERE id = %s;", (session["user_id"],)
                )
                user = cursor.fetchone()
                if user is None or user["role"] not in STAFF_ROLES:
                    raise HTTPException(
                        status_code=403, detail="Agent or admin access is required."
                    )

            tokens = create_access_token(session["user_id"])

            cursor.execute(
                """
                UPDATE auth_sessions
                SET refresh_token_hash = %s
                WHERE id = %s;
                """,
                (session_token_hash(new_token, staff), session["id"]),
            )

    # Preserve the original session expiry when rotating the token.
    set_refresh_cookie(response, new_token, session["expires_at"], staff=staff)
    return tokens


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    _: None = Depends(require_session_header),
):
    return revoke_session(request, response, staff=False)


@router.post("/staff/logout")
def staff_logout(
    request: Request, response: Response, _: None = Depends(require_session_header)
):
    return revoke_session(request, response, staff=True)


def revoke_session(request: Request, response: Response, *, staff: bool):
    current_token = request.cookies.get(STAFF_COOKIE_NAME if staff else COOKIE_NAME)

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
                    (session_token_hash(current_token, staff),),
                )

    response.delete_cookie(
        key=STAFF_COOKIE_NAME if staff else COOKIE_NAME,
        path="/auth/staff" if staff else "/auth",
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
            options={
                "require": [
                    "sub",
                    "iat",
                    "exp",
                    "iss",
                    "aud",
                    "token_type",
                ]
            },
        )

        if payload["token_type"] != "access":
            raise unauthorized

        user_id = UUID(payload["sub"])

    except (InvalidTokenError, ValueError, TypeError, AttributeError):
        raise unauthorized

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Read the current role from the database on each request.
            cursor.execute(
                """
                SELECT id, name, email, role, created_at
                FROM users
                WHERE id = %s;
                """,
                (user_id,),
            )

            user = cursor.fetchone()

    if user is None:
        raise unauthorized

    return user


def require_staff(user: dict = Depends(get_current_user)):
    if user["role"] not in STAFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Agent or admin access is required.",
        )

    return user


def require_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access is required.",
        )

    return user


@router.get("/me", response_model=UserResponse)
def me(user: dict = Depends(get_current_user)):
    return user


@router.get("/staff/me", response_model=UserResponse)
def staff_me(user: dict = Depends(require_staff)):
    return user


@router.get("/admin/me", response_model=UserResponse)
def admin_me(user: dict = Depends(require_admin)):
    return user
