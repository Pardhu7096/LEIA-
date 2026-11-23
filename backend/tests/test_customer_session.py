"""Customer session regression tests that run without PostgreSQL.

An in-memory stand-in for the two auth tables lets these tests run anywhere.
Every real database connection is blocked, so backend/.env is never used.
"""

import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault(
    "JWT_SECRET", "isolated-test-secret-never-use-in-production-12345"
)

import auth  # noqa: E402
import database  # noqa: E402
import native_sessions  # noqa: E402
from rate_limits import login_limit, registration_limit  # noqa: E402

PASSWORD = "customer-test-password"
CSRF = {"X-LEIA-CSRF": "1"}


class FakeAuthDatabase:
    """Implements only the auth queries in auth.py, matched by their SQL text."""

    def __init__(self):
        self.users = {}
        self.sessions = []

    def add_user(self, role="customer"):
        user = {
            "id": uuid4(),
            "name": "Session tester",
            "email": f"{uuid4().hex}@example.com",
            "role": role,
            "created_at": datetime.now(timezone.utc),
            "password_hash": auth.password_hasher.hash(PASSWORD),
        }
        self.users[user["id"]] = user
        return user

    def live_sessions(self):
        now = datetime.now(timezone.utc)
        return [
            s for s in self.sessions
            if s["revoked_at"] is None and s["expires_at"] > now
        ]

    def connect(self):
        return FakeConnection(self)


class FakeConnection:
    """Mirrors psycopg: commit on clean exit, roll back if the block raises."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.snapshot = copy.deepcopy(self.db.sessions)
        return self

    def __exit__(self, exc_type, *exc):
        if exc_type is not None:
            self.db.sessions[:] = self.snapshot
        return False

    def cursor(self):
        return FakeCursor(self.db)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetchone(self):
        return self.result

    def execute(self, query, params=()):
        q = " ".join(query.split())
        now = datetime.now(timezone.utc)
        self.result = None

        if q.startswith("SELECT id, password_hash, role FROM users WHERE email"):
            self.result = next(
                (u for u in self.db.users.values() if u["email"] == params[0]), None
            )
        elif q.startswith("SELECT id, name, email, role, created_at FROM users"):
            self.result = self.db.users.get(params[0])
        elif q.startswith("SELECT role FROM users WHERE id"):
            self.result = self.db.users.get(params[0])
        elif q.startswith("INSERT INTO auth_sessions"):
            user_id, token_hash, expires_at = params
            self.db.sessions.append({
                "id": uuid4(),
                "user_id": user_id,
                "refresh_token_hash": token_hash,
                "expires_at": expires_at,
                "revoked_at": None,
                "previous_refresh_token_hash": None,
                "rotation_request_hash": None,
                "rotated_at": None,
                "revoke_reason": None,
            })
        elif q.startswith("SELECT id, user_id, expires_at, revoked_at, refresh_token_hash"):
            self.result = next(
                (dict(s) for s in self.db.sessions
                 if params[0] in (s["refresh_token_hash"],
                                  s["previous_refresh_token_hash"])),
                None,
            )
        elif q.startswith("UPDATE auth_sessions SET refresh_token_hash = %s, previous_refresh_token_hash"):
            new_hash, previous, attempt, rotated_at, session_id = params
            for s in self.db.sessions:
                if s["id"] == session_id:
                    s.update(refresh_token_hash=new_hash,
                             previous_refresh_token_hash=previous,
                             rotation_request_hash=attempt, rotated_at=rotated_at)
        elif q.startswith("UPDATE auth_sessions SET revoked_at = %s, revoke_reason = 'reuse_detected'"):
            revoked_at, session_id = params
            for s in self.db.sessions:
                if s["id"] == session_id and s["revoked_at"] is None:
                    s.update(revoked_at=revoked_at, revoke_reason="reuse_detected")
        elif q.startswith("UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP, revoke_reason = 'logout'"):
            for s in self.db.sessions:
                if s["revoked_at"] is None and params[0] in (
                    s["refresh_token_hash"], s["previous_refresh_token_hash"]
                ):
                    s.update(revoked_at=now, revoke_reason="logout")
        elif q.startswith("UPDATE auth_sessions SET revoked_at"):
            for s in self.db.sessions:
                if s["refresh_token_hash"] == params[0] and s["revoked_at"] is None:
                    s["revoked_at"] = now
        elif q.startswith("SELECT id, user_id, expires_at FROM auth_sessions"):
            self.result = next(
                (s for s in self.db.live_sessions()
                 if s["refresh_token_hash"] == params[0]),
                None,
            )
        elif q.startswith("UPDATE auth_sessions SET refresh_token_hash"):
            new_hash, session_id = params
            for s in self.db.sessions:
                if s["id"] == session_id:
                    s["refresh_token_hash"] = new_hash
        else:
            raise AssertionError(f"Unexpected auth query: {q[:80]}")
        return self


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeAuthDatabase()

    def blocked(*_args, **_kwargs):
        raise AssertionError("Tests must never open a real database connection.")

    monkeypatch.setattr(database, "get_connection", blocked)
    monkeypatch.setattr(auth, "get_connection", db.connect)
    monkeypatch.setattr(native_sessions, "get_connection", db.connect)
    return db


@pytest.fixture
def app(fake_db):
    application = FastAPI()
    application.include_router(auth.router)
    application.include_router(native_sessions.router)
    application.dependency_overrides[login_limit] = lambda: None
    application.dependency_overrides[registration_limit] = lambda: None
    return application


def login(client, user):
    return client.post(
        "/auth/login", json={"email": user["email"], "password": PASSWORD}
    )


# ---------------------------------------------------------------------------
# Phase 1 reproduction: what the current contract gives a native client.
# ---------------------------------------------------------------------------


def test_repro_login_gives_client_nothing_it_can_persist(app, fake_db):
    """The refresh credential exists only as an HttpOnly cookie.

    A React Native app cannot read it, so it cannot save it to secure storage.
    The session survives an app restart only if the platform cookie jar
    happens to persist it.
    """
    user = fake_db.add_user()
    with TestClient(app) as client:
        response = login(client, user)

    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "token_type", "expires_in"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "path=/auth" in cookie


def test_repro_restart_without_cookie_jar_loses_a_live_session(app, fake_db):
    """Simulates an app restart where the native cookie jar was not saved."""
    user = fake_db.add_user()
    with TestClient(app) as first_launch:
        assert login(first_launch, user).status_code == 200

    # Restart: a new process, no cookie, while the server session is still valid.
    with TestClient(app) as second_launch:
        restored = second_launch.post("/auth/refresh", headers=CSRF)

    assert len(fake_db.live_sessions()) == 1
    assert fake_db.live_sessions()[0]["expires_at"] > datetime.now(timezone.utc) + timedelta(days=6)
    assert restored.status_code == 401  # Reproduced: valid session, forced re-login.


def test_repro_cookie_is_samesite_lax_so_cross_site_browser_fetch_omits_it(app, fake_db):
    """Browser case: a web build on localhost:8081 calling onrender.com is cross-site.

    Browsers do not attach SameSite=Lax cookies to cross-site fetch() requests,
    so /auth/refresh from that page always receives no cookie and returns 401.
    """
    user = fake_db.add_user()
    with TestClient(app) as client:
        response = login(client, user)

    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_existing_cookie_rotation_and_replay_rejection(app, fake_db):
    """Baseline the browser cookie behavior that must stay unchanged."""
    user = fake_db.add_user()
    with TestClient(app) as client:
        assert login(client, user).status_code == 200
        original = client.cookies.get(auth.COOKIE_NAME)

        assert client.post("/auth/refresh").status_code == 403  # CSRF header required
        assert client.post(
            "/auth/refresh", headers={**CSRF, "Origin": "https://evil.example"}
        ).status_code == 403
        assert client.post("/auth/refresh", headers=CSRF).status_code == 200
        rotated = client.cookies.get(auth.COOKIE_NAME)
        assert rotated and rotated != original

    with TestClient(app) as attacker:
        replay = attacker.post(
            "/auth/refresh",
            headers={**CSRF, "Cookie": f"{auth.COOKIE_NAME}={original}"},
        )
        assert replay.status_code == 401
        staff_replay = attacker.post(
            "/auth/staff/refresh",
            headers={**CSRF, "Cookie": f"{auth.STAFF_COOKIE_NAME}={rotated}"},
        )
        assert staff_replay.status_code == 401  # customer token rejected as staff


def test_existing_cookie_logout_revokes_and_expiry_is_enforced(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        assert login(client, user).status_code == 200
        token = client.cookies.get(auth.COOKIE_NAME)
        assert client.post("/auth/logout", headers=CSRF).status_code == 200
    assert fake_db.live_sessions() == []

    with TestClient(app) as client:
        response = client.post(
            "/auth/refresh", headers={**CSRF, "Cookie": f"{auth.COOKIE_NAME}={token}"}
        )
        assert response.status_code == 401

        assert login(client, user).status_code == 200
        fake_db.sessions[-1]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert client.post("/auth/refresh", headers=CSRF).status_code == 401


# ---------------------------------------------------------------------------
# Native (Android) session contract: /auth/native/*
# ---------------------------------------------------------------------------

NATIVE = "/auth/native"


def request_id():
    return uuid4().hex + uuid4().hex[:8]


def native_login(client, user, password=PASSWORD, headers=None):
    return client.post(
        f"{NATIVE}/login",
        json={"email": user["email"], "password": password},
        headers=headers or {},
    )


def native_refresh(client, token, rid=None, headers=None):
    return client.post(
        f"{NATIVE}/refresh",
        json={"refresh_token": token, "request_id": rid or request_id()},
        headers=headers or {},
    )


def session_for(db, user):
    return next(s for s in db.sessions if s["user_id"] == user["id"])


def test_native_login_returns_body_token_and_sets_no_cookie(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        response = native_login(client, user)

    assert response.status_code == 200
    body = response.json()
    assert {"access_token", "refresh_token", "refresh_expires_at"} <= set(body)
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    stored = session_for(fake_db, user)["refresh_token_hash"]
    assert stored == native_sessions.native_hash(body["refresh_token"])
    assert stored != auth.token_hash(body["refresh_token"])  # separate namespace


def test_native_login_rejects_bad_credentials_and_uses_login_rate_limit(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        assert native_login(client, user, password="wrong-password").status_code == 401
        assert native_login(client, {"email": "nobody@example.com"}).status_code == 401

        def limited():
            raise auth.HTTPException(429, "Request limit reached.")

        app.dependency_overrides[login_limit] = limited
        assert native_login(client, user).status_code == 429
    assert fake_db.sessions == []


def test_native_restart_restores_session_from_stored_token(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as first_launch:
        login_body = native_login(first_launch, user).json()

    # App restart: a new process with no cookies, only the stored token.
    with TestClient(app) as second_launch:
        restored = native_refresh(second_launch, login_body["refresh_token"])
        assert restored.status_code == 200
        body = restored.json()
        assert body["refresh_token"] != login_body["refresh_token"]
        assert body["refresh_expires_at"] == login_body["refresh_expires_at"]  # no extension
        me = second_launch.get(
            "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert me.status_code == 200
        assert me.json()["email"] == user["email"]


def test_native_rotation_chain_and_old_token_reuse_revokes_session(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        first = native_login(client, user).json()["refresh_token"]
        second = native_refresh(client, first).json()["refresh_token"]
        third = native_refresh(client, second).json()["refresh_token"]
        assert len({first, second, third}) == 3

        # The just-replaced token is replayed by someone else (new request_id).
        assert native_refresh(client, second).status_code == 401
        session = session_for(fake_db, user)
        assert session["revoked_at"] is not None
        assert session["revoke_reason"] == "reuse_detected"  # persisted despite the 401
        # The legitimate current token is now dead too.
        assert native_refresh(client, third).status_code == 401
        # Tokens older than one rotation are simply unknown.
        assert native_refresh(client, first).status_code == 401


def test_native_retry_of_same_attempt_is_idempotent(app, fake_db):
    """A lost response or cold-start timeout must not log the user out."""
    user = fake_db.add_user()
    with TestClient(app) as client:
        token = native_login(client, user).json()["refresh_token"]
        rid = request_id()
        first = native_refresh(client, token, rid)
        retry = native_refresh(client, token, rid)

        assert first.status_code == retry.status_code == 200
        assert retry.json()["refresh_token"] == first.json()["refresh_token"]
        assert session_for(fake_db, user)["revoked_at"] is None
        # The re-issued token continues the chain normally.
        assert native_refresh(client, retry.json()["refresh_token"]).status_code == 200


def test_native_retry_after_grace_window_counts_as_reuse(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        token = native_login(client, user).json()["refresh_token"]
        rid = request_id()
        assert native_refresh(client, token, rid).status_code == 200
        session_for(fake_db, user)["rotated_at"] -= timedelta(
            seconds=native_sessions.RETRY_GRACE_SECONDS + 1
        )
        assert native_refresh(client, token, rid).status_code == 401
        assert session_for(fake_db, user)["revoke_reason"] == "reuse_detected"


def test_native_expired_session_is_rejected(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        token = native_login(client, user).json()["refresh_token"]
        session_for(fake_db, user)["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert native_refresh(client, token).status_code == 401
    assert session_for(fake_db, user)["revoke_reason"] is None


def test_native_logout_revokes_current_or_just_rotated_token(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        token = native_login(client, user).json()["refresh_token"]
        assert client.post(f"{NATIVE}/logout", json={"refresh_token": token}).status_code == 200
        assert session_for(fake_db, user)["revoke_reason"] == "logout"
        assert native_refresh(client, token).status_code == 401

        # Logging out with the previous token (new token never saved) still works.
        other = fake_db.add_user()
        old = native_login(client, other).json()["refresh_token"]
        new = native_refresh(client, old).json()["refresh_token"]
        assert client.post(f"{NATIVE}/logout", json={"refresh_token": old}).status_code == 200
        assert native_refresh(client, new).status_code == 401

        # Unknown tokens get the same answer (no session oracle).
        unknown = client.post(f"{NATIVE}/logout", json={"refresh_token": "x" * 64})
        assert unknown.status_code == 200


@pytest.mark.parametrize("marker", [
    {"Origin": "http://localhost:8081"},
    {"Origin": "https://leia-backend-staging.onrender.com"},
    {"Sec-Fetch-Site": "same-origin"},
])
def test_native_endpoints_refuse_browser_requests(app, fake_db, marker):
    user = fake_db.add_user()
    with TestClient(app) as client:
        token = native_login(client, user).json()["refresh_token"]
        assert native_login(client, user, headers=marker).status_code == 403
        assert native_refresh(client, token, headers=marker).status_code == 403
        logout = client.post(f"{NATIVE}/logout", json={"refresh_token": token}, headers=marker)
        assert logout.status_code == 403
    assert session_for(fake_db, user)["revoked_at"] is None


def test_native_and_cookie_tokens_are_not_interchangeable(app, fake_db):
    user = fake_db.add_user()
    with TestClient(app) as client:
        native_token = native_login(client, user).json()["refresh_token"]
        assert login(client, user).status_code == 200
        cookie_token = client.cookies.get(auth.COOKIE_NAME)

    with TestClient(app) as client:
        assert client.post("/auth/refresh", headers={
            **CSRF, "Cookie": f"{auth.COOKIE_NAME}={native_token}",
        }).status_code == 401
        assert native_refresh(client, cookie_token).status_code == 401
        # A cookie alone never authenticates the native endpoint.
        no_body = client.post(f"{NATIVE}/refresh", headers={
            "Cookie": f"{auth.COOKIE_NAME}={cookie_token}",
        })
        assert no_body.status_code == 422
    # Probing with the cookie token must not have revoked anything.
    assert all(s["revoked_at"] is None for s in fake_db.sessions)


@pytest.mark.parametrize("body", [
    {"refresh_token": "short", "request_id": "a" * 32},
    {"refresh_token": "x" * 64, "request_id": "bad id with spaces"},
    {"refresh_token": "x" * 64, "request_id": "a" * 31},  # below the 32-char minimum
    {"refresh_token": "x" * 64},
    {"refresh_token": "x" * 64, "request_id": "a" * 32, "extra": True},
])
def test_native_refresh_validates_input(app, fake_db, body):
    with TestClient(app) as client:
        assert client.post(f"{NATIVE}/refresh", json=body).status_code == 422


def test_native_routes_are_mounted_in_the_application():
    import importlib

    main = importlib.import_module("main")
    paths = set(main.app.openapi()["paths"])
    assert {f"{NATIVE}/login", f"{NATIVE}/refresh", f"{NATIVE}/logout"} <= paths
    # Existing web and Agent CRM contracts are still present.
    assert {"/auth/login", "/auth/refresh", "/auth/logout",
            "/auth/staff/login", "/auth/staff/refresh", "/auth/staff/logout"} <= paths
