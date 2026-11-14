"""Deployment-readiness regressions: configuration rules, an empty-database build
and the readiness probe. Database tests need TEST_DATABASE_URL and a role that
may create a scratch database; they are skipped otherwise."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault(
    "JWT_SECRET", "isolated-test-secret-never-use-in-production-12345"
)
import preflight

VALID = {
    "JWT_SECRET": "x" * 40,
    "GROQ_API_KEY": "test-only",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "leia",
    "DB_USER": "leia_app",
    "DB_PASSWORD": "test-only",
    "COOKIE_SECURE": "true",
    "LEIA_ALLOWED_ORIGINS": "https://app.example.test, https://crm.example.test/",
}


def test_complete_deployed_configuration_passes():
    assert preflight.environment_problems(VALID, deployed=True) == []


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"JWT_SECRET": "short"}, "JWT_SECRET"),
        ({"JWT_SECRET": None}, "JWT_SECRET"),
        ({"GROQ_API_KEY": " "}, "GROQ_API_KEY"),
        ({"DB_PASSWORD": None}, "DB_PASSWORD"),
        ({"DB_PORT": "postgres"}, "DB_PORT"),
        ({"DB_PORT": "0"}, "DB_PORT"),
        ({"COOKIE_SECURE": "false"}, "COOKIE_SECURE"),
        ({"COOKIE_SECURE": None}, "COOKIE_SECURE"),
        ({"LEIA_ALLOWED_ORIGINS": None}, "LEIA_ALLOWED_ORIGINS"),
        ({"LEIA_ALLOWED_ORIGINS": "http://app.example.test"}, "LEIA_ALLOWED_ORIGINS"),
        ({"LEIA_ALLOWED_ORIGINS": "https://app.example.test,http://localhost:8081"},
         "LEIA_ALLOWED_ORIGINS"),
        ({"LEIA_ALLOWED_ORIGINS": "https://localhost"}, "LEIA_ALLOWED_ORIGINS"),
        ({"LEIA_ALLOWED_ORIGINS": "https://<customer-web-domain>"}, "LEIA_ALLOWED_ORIGINS"),
        ({"LEIA_ALLOWED_ORIGINS": "https://app.example.test/login"}, "LEIA_ALLOWED_ORIGINS"),
        ({"DB_PASSWORD": "CHANGE_ME_database_password"}, "DB_PASSWORD"),
        ({"JWT_SECRET": "CHANGE_ME_" + "x" * 40}, "JWT_SECRET"),
        ({"DB_HOST": "db.internal.example"}, "DB_SSLMODE"),
        ({"DB_HOST": "10.0.1.5", "DB_SSLMODE": "prefer"}, "DB_SSLMODE"),
    ],
)
def test_unsafe_deployed_configuration_is_rejected(changes, expected):
    env = {key: value for key, value in {**VALID, **changes}.items() if value is not None}
    problems = preflight.environment_problems(env, deployed=True)
    assert problems and any(expected in problem for problem in problems)


def test_problems_name_settings_but_never_print_values():
    secret = "CHANGE_ME_super-secret-value-that-must-not-be-echoed-000"
    env = {**VALID, "JWT_SECRET": secret, "DB_PASSWORD": secret, "GROQ_API_KEY": secret}
    problems = " ".join(preflight.environment_problems(env, deployed=True))
    assert "JWT_SECRET" in problems and secret not in problems


def test_local_development_does_not_need_server_only_settings():
    env = {key: value for key, value in VALID.items()
           if key not in {"COOKIE_SECURE", "LEIA_ALLOWED_ORIGINS"}}
    assert preflight.environment_problems(env) == []


def test_missing_maps_keys_are_a_warning_not_a_failure():
    assert "GOOGLE_PLACES_API_KEY" in preflight.environment_warnings(VALID)[0]
    assert preflight.environment_warnings(
        {**VALID, "GOOGLE_PLACES_API_KEY": "k", "GOOGLE_ROUTES_API_KEY": "k"}
    ) == []


def test_unreachable_database_is_reported_without_connection_details():
    def refuse():
        raise psycopg.OperationalError("connection to host db.internal user leia_app failed")

    problems = preflight.database_problems(refuse)
    assert problems == ["Database is unreachable or misconfigured (OperationalError)."]


def run_backend(code, **env):
    """Import the application in a clean interpreter with the given settings."""
    settings = {
        "PATH": os.environ["PATH"],
        "JWT_SECRET": "isolated-test-secret-never-use-in-production-12345",
        "GROQ_API_KEY": "test-only",
        # A closed port: these checks must finish before any database access.
        "DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_NAME": "none",
        "DB_USER": "none", "DB_PASSWORD": "none",
        **env,
    }
    return subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=settings,
        capture_output=True, text=True, timeout=120,
    )


ORIGIN_CHECK = """
from fastapi.testclient import TestClient
import main
http = TestClient(main.app)
def preflight_request(origin):
    return http.options("/auth/login", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST"})
def refresh(origin):
    return http.post("/auth/refresh", headers={"Origin": origin, "X-LEIA-CSRF": "1"})
for origin in ("https://crm.example.test", "http://localhost:8082", "https://evil.example.test"):
    print(origin, preflight_request(origin).headers.get("access-control-allow-origin"),
          refresh(origin).status_code)
"""


def test_configured_origins_replace_the_local_development_defaults():
    result = run_backend(
        ORIGIN_CHECK, LEIA_ALLOWED_ORIGINS="https://app.example.test,https://crm.example.test/"
    )
    assert result.returncode == 0, result.stderr
    # 401 = origin accepted, no session cookie; 403 = origin refused.
    assert result.stdout.splitlines() == [
        "https://crm.example.test https://crm.example.test 401",
        "http://localhost:8082 None 403",
        "https://evil.example.test None 403",
    ]


def test_local_origins_still_work_when_nothing_is_configured():
    result = run_backend(ORIGIN_CHECK)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "https://crm.example.test None 403",
        "http://localhost:8082 http://localhost:8082 401",
        "https://evil.example.test None 403",
    ]


def test_liveness_answers_but_readiness_fails_when_database_is_down():
    result = run_backend("""
from fastapi.testclient import TestClient
import main
http = TestClient(main.app)
print(http.get("/health").status_code, http.get("/health/ready").status_code)
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "200 503"


@pytest.fixture
def fresh_database(monkeypatch):
    """An empty scratch database, reached through the application's own settings."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated disposable database.")
    name = f"leia_scratch_{uuid4().hex}"
    try:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error as error:
        pytest.skip(f"Cannot create a scratch database ({type(error).__name__}).")
    target = conninfo_to_dict(url)
    for setting, value in {
        "DB_HOST": target.get("host", "localhost"),
        "DB_PORT": str(target.get("port", 5432)),
        "DB_NAME": name,
        "DB_USER": target.get("user", "postgres"),
        "DB_PASSWORD": target.get("password", ""),
    }.items():
        monkeypatch.setenv(setting, value)
    monkeypatch.delenv("DB_SSLMODE", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    try:
        yield name
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_empty_database_is_built_by_migrations_and_serves_the_api(fresh_database):
    import database
    import migrate_crm

    assert preflight.database_problems()[0].startswith("Missing tables (users")
    migrate_crm.migrate()
    migrate_crm.migrate()  # A tracked rerun must be harmless.
    assert preflight.database_problems() == []
    with database.get_connection() as connection:
        applied = [row["name"] for row in connection.execute(
            "SELECT name FROM public.crm_schema_migrations ORDER BY name").fetchall()]
    assert applied == sorted(path.name for path in preflight.MIGRATIONS.glob("*.sql"))
    assert applied[0] == "000_core_baseline.sql"

    import main

    password = "correct-horse-battery-1"
    csrf = {"X-LEIA-CSRF": "1"}
    with TestClient(main.app) as http:
        assert http.get("/health/ready").json() == {"status": "ready"}
        account = {"name": "Asha", "email": "Asha@Example.com", "password": password}
        registered = http.post("/auth/register", json=account)
        assert registered.status_code == 201, registered.text
        assert registered.json()["role"] == "customer"
        assert http.post("/auth/register", json=account).status_code == 409

        credentials = {"email": "asha@example.com", "password": password}
        assert http.post("/auth/login", json={**credentials, "password": "wrong"}).status_code == 401
        login = http.post("/auth/login", json=credentials)
        assert login.status_code == 200, login.text
        customer = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert http.get("/auth/me", headers=customer).json()["email"] == "asha@example.com"
        assert http.post("/auth/refresh", headers=csrf).status_code == 200

        # Customers are kept out of every staff route.
        assert http.post("/auth/staff/login", json=credentials).status_code == 403
        assert http.get("/staff/properties", headers=customer).status_code == 403
        assert http.get("/auth/admin/me", headers=customer).status_code == 403

        conversation = http.post("/conversations", headers=customer)
        assert conversation.status_code == 201, conversation.text
        conversation_id = conversation.json()["id"]
        assert conversation.json()["title"] == "New property search"
        # A selected-listing question is answered without the AI provider.
        chat = http.post("/chat", headers=customer, json={
            "message": "What is the price?", "conversation_id": conversation_id,
            "property_id": str(uuid4()),
        })
        assert chat.status_code == 200, chat.text
        saved = http.get(f"/conversations/{conversation_id}", headers=customer).json()
        assert [message["role"] for message in saved["messages"]] == ["user", "assistant"]
        assert len(http.get("/conversations", headers=customer).json()["conversations"]) == 1
        assert http.delete(f"/conversations/{conversation_id}", headers=customer).status_code == 204

        # Staff accounts are promoted in the database; there is no public route for it.
        with database.get_connection() as connection:
            connection.execute("UPDATE users SET role = 'agent' WHERE email = 'asha@example.com'")
        staff_login = http.post("/auth/staff/login", json=credentials)
        assert staff_login.status_code == 200, staff_login.text
        agent = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}
        created = http.post("/staff/properties", headers=agent, json={
            "title": "Scratch flat", "property_type": "flat"})
        assert created.status_code == 201, created.text
        assert http.get("/properties").json() == {"properties": []}  # Drafts stay private.
        assert http.post(
            f"/staff/properties/{created.json()['id']}/approve", headers=agent,
            json={"version": created.json()["version"]},
        ).status_code == 403  # Publishing needs an admin.

        # A release that ships a migration the database has not received is not ready.
        with database.get_connection() as connection:
            connection.execute(
                "DELETE FROM public.crm_schema_migrations WHERE name = '007_room_panoramas.sql'")
        assert http.get("/health").status_code == 200
        pending = http.get("/health/ready")
        assert pending.status_code == 503
        assert pending.json() == {"detail": "LEIA backend is not ready."}
        assert preflight.database_problems() == [
            "Pending migrations (007_room_panoramas.sql): run migrate_crm.py."]


@pytest.mark.parametrize("host,sslmode", [
    ("127.0.0.1", None), ("localhost", None), ("::1", None), ("/var/run/postgresql", None),
    ("db.internal.example", "require"), ("10.0.1.5", "verify-full"), ("db.example", "VERIFY-CA"),
])
def test_local_or_encrypted_database_connections_pass(host, sslmode):
    settings = {**VALID, "DB_HOST": host}
    if sslmode:
        settings["DB_SSLMODE"] = sslmode
    assert preflight.environment_problems(settings, deployed=True) == []
