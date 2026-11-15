"""M12: the API works with exactly api_privileges.REQUIRED and nothing more.

A scratch database is shaped like the development one (the API role owns the
database and four core tables), the generated plan is executed, and then the
whole API runs as that role: accounts, sessions, saved chats, listings with
private details, photos, panoramas, room panoramas, approval, public search and
rate limits. Finally the role is shown unable to change the schema or the
migration history. Needs TEST_DATABASE_URL with a role that may create databases
and roles; skipped otherwise."""

import os
from io import BytesIO
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psycopg.conninfo import conninfo_to_dict

from test_core_schema import scratch  # noqa: F401

os.environ.setdefault("JWT_SECRET", "isolated-test-secret-never-use-in-production-12345")
import api_privileges  # noqa: E402

DEV_OWNED_TABLES = ("users", "auth_sessions", "conversations", "chat_messages")


def jpeg(width, height, colour="green"):
    buffer = BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def api_role(scratch, monkeypatch):  # noqa: F811
    """A development-shaped database and a login role that owns what leia_app owns there."""
    import migrate_crm

    migrate_crm.migrate()
    role, password = f"api_{uuid4().hex[:12]}", uuid4().hex
    with scratch(autocommit=True) as connection:
        database = connection.info.dbname
        superuser = connection.execute("SELECT current_user AS u").fetchone()["u"]
        connection.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        connection.execute(f'ALTER DATABASE "{database}" OWNER TO {role}')
        for table in DEV_OWNED_TABLES:
            connection.execute(f"ALTER TABLE public.{table} OWNER TO {role}")
    try:
        yield {"role": role, "password": password, "database": database, "owner": superuser}
    finally:
        with scratch(autocommit=True) as connection:
            connection.execute(f'ALTER DATABASE "{database}" OWNER TO CURRENT_USER')
            connection.execute(f"REASSIGN OWNED BY {role} TO CURRENT_USER")
            connection.execute(f"DROP OWNED BY {role}")
        url = os.environ["TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            admin.execute(f"DROP ROLE IF EXISTS {role}")


def test_plan_makes_the_role_least_privileged_and_the_api_still_works(
        api_role, scratch, monkeypatch):  # noqa: F811
    role = api_role["role"]
    with scratch() as connection:
        before = api_privileges.inventory(connection, role)
    found = api_privileges.problems(before)
    for expected in (f"owns database {api_role['database']}", "owns table users",
                     "users: not needed DELETE, REFERENCES, TRIGGER, TRUNCATE, UPDATE",
                     "may CREATE objects in schema public", "crm_schema_migrations: missing SELECT"):
        assert expected in found

    sql = api_privileges.plan(before, api_role["owner"])
    assert "ALTER SEQUENCE" not in sql  # the identity sequence moves with its table
    with scratch(autocommit=True) as connection:
        connection.execute(sql)
    with scratch() as connection:
        after = api_privileges.inventory(connection, role)
    assert api_privileges.problems(after) == []
    assert {t: set(i["privileges"]) for t, i in after["tables"].items()} == api_privileges.REQUIRED
    assert all(i["owner"] == api_role["owner"] for i in after["tables"].values())
    # Running the plan again changes nothing (it is safe to rerun after a partial review).
    with scratch(autocommit=True) as connection:
        connection.execute(api_privileges.plan(after, api_role["owner"]))
    with scratch() as connection:
        assert api_privileges.inventory(connection, role) == after

    # From here on, the API runs as the least-privileged role.
    monkeypatch.setenv("DB_USER", role)
    monkeypatch.setenv("DB_PASSWORD", api_role["password"])
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    import main

    def promote(email, new_role):
        with scratch() as connection:
            connection.execute("UPDATE users SET role = %s WHERE email = %s", (new_role, email))

    csrf = {"X-LEIA-CSRF": "1"}
    with TestClient(main.app) as http:
        assert http.get("/health/ready").json() == {"status": "ready"}  # reads migration history

        def account(name):
            details = {"name": name, "email": f"{name.lower()}@example.com",
                       "password": "correct-horse-battery-1"}
            assert http.post("/auth/register", json=details).status_code == 201
            return {"email": details["email"], "password": details["password"]}

        customer, agent, admin = account("Asha"), account("Ravi"), account("Meena")
        login = http.post("/auth/login", json=customer)
        assert login.status_code == 200, login.text
        bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert http.get("/auth/me", headers=bearer).status_code == 200
        assert http.post("/auth/refresh", headers=csrf).status_code == 200  # session row lock + update

        # Saved chats: create, chat (identity insert without sequence rights), list, read, delete.
        chat_id = http.post("/conversations", headers=bearer).json()["id"]
        sent = http.post("/chat", headers=bearer, json={
            "message": "What is the price?", "conversation_id": chat_id, "property_id": str(uuid4())})
        assert sent.status_code == 200, sent.text
        assert len(http.get(f"/conversations/{chat_id}", headers=bearer).json()["messages"]) == 2
        assert len(http.get("/conversations", headers=bearer).json()["conversations"]) == 1
        assert http.delete(f"/conversations/{chat_id}", headers=bearer).status_code == 204
        assert http.post("/auth/logout", headers=csrf).status_code in (200, 204)

        promote(agent["email"], "agent")
        promote(admin["email"], "admin")

        def staff(credentials):
            response = http.post("/auth/staff/login", json=credentials)
            assert response.status_code == 200, response.text
            return {"Authorization": f"Bearer {response.json()['access_token']}"}

        owner, approver = staff(agent), staff(admin)
        payload = {"title": "Least privilege flat", "property_type": "flat",
                   "locality": "Madhurawada", "city": "Visakhapatnam", "price": 7500000,
                   "area_sqft": 1130, "bedrooms": 3, "owner_phone": "private phone",
                   "agent_notes": "private notes", "listing_consent": True}
        created = http.post("/staff/properties", json=payload, headers=owner)
        assert created.status_code == 201, created.text
        listing = created.json()
        path = f"/staff/properties/{listing['id']}"
        updated = http.put(path, json={**payload, "version": listing["version"],
                                       "owner_phone": "changed"}, headers=owner)
        assert updated.status_code == 200, updated.text  # private details upsert
        version = updated.json()["version"]

        def step(response, expected=200):
            assert response.status_code == expected, response.text
            return response.json()["version"]

        cover, panorama = jpeg(600, 400), jpeg(1024, 512, "blue")
        version = step(http.put(f"{path}/media/cover", params={"version": version},
                                content=cover, headers=owner))
        version = step(http.delete(f"{path}/media/cover", params={"version": version}, headers=owner))
        version = step(http.put(f"{path}/media/cover", params={"version": version},
                                content=cover, headers=owner))
        version = step(http.put(f"{path}/media/panorama", params={"version": version},
                                content=panorama, headers=owner))
        photos = []
        for room in ("Kitchen", "Hall"):
            upload = http.post(f"{path}/gallery", params={"version": version, "room": room},
                               content=cover, headers=owner)
            version = step(upload, 201)
            photos.append(upload.json()["id"])
        version = step(http.put(f"{path}/gallery/{photos[0]}/cover", params={"version": version},
                                headers=owner))
        version = step(http.delete(f"{path}/gallery/{photos[1]}", params={"version": version},
                                   headers=owner))
        room_url = f"{path}/panoramas/Bedroom%201"
        version = step(http.put(room_url, params={"version": version}, content=panorama, headers=owner))
        version = step(http.put(f"{path}/panoramas/Hall", params={"version": version},
                                content=panorama, headers=owner))
        version = step(http.delete(f"{path}/panoramas/Hall", params={"version": version}, headers=owner))

        submitted = http.post(f"{path}/submit", json={"version": version}, headers=owner)
        assert submitted.status_code == 200, submitted.text
        approved = http.post(f"{path}/approve", json={"version": submitted.json()["version"]},
                             headers=approver)
        assert approved.status_code == 200, approved.text

        cards = http.get("/properties").json()["properties"]
        assert [card["id"] for card in cards] == [listing["id"]]
        assert http.get(cards[0]["image"]).status_code == 200
        assert http.get(cards[0]["tour"]).status_code == 200
        assert len(http.get(f"/properties/{listing['id']}/gallery").json()) == 1
        assert len(http.get(f"/properties/{listing['id']}/panoramas").json()) == 1

        # Rate limits are stored in request_limits (insert, upsert, expiry clean-up).
        statuses = [http.post("/auth/login", json={**customer, "password": "wrong"}).status_code
                    for _ in range(15)]
        assert statuses[0] == 401 and 429 in statuses

    # Everything the role does not need is refused by the database.
    target = conninfo_to_dict(os.environ["TEST_DATABASE_URL"])
    forbidden = [
        "CREATE TABLE public.stray (id int)",
        "CREATE SCHEMA stray",
        "ALTER TABLE public.users ADD COLUMN stray int",
        "DROP TABLE public.chat_messages",
        "TRUNCATE public.users",
        "UPDATE public.users SET role = 'admin'",
        "DELETE FROM public.users",
        "INSERT INTO public.crm_schema_migrations (name) VALUES ('999_fake.sql')",
        "DELETE FROM public.crm_schema_migrations",
        "UPDATE public.chat_messages SET content = 'x'",
        "SELECT nextval('public.chat_messages_id_seq')",
    ]
    for statement in forbidden:
        with psycopg.connect(host=target.get("host"), port=target.get("port"),
                             dbname=api_role["database"], user=role,
                             password=api_role["password"]) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


def test_plan_refuses_roles_with_objects_it_does_not_understand(api_role, scratch):  # noqa: F811
    role = api_role["role"]
    with scratch(autocommit=True) as connection:
        connection.execute("CREATE FUNCTION public.stray() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        connection.execute(f"ALTER FUNCTION public.stray() OWNER TO {role}")
    with scratch() as connection:
        report = api_privileges.inventory(connection, role)
    assert "owns function stray()" in api_privileges.problems(report)
    with pytest.raises(SystemExit, match="manual review"):
        api_privileges.plan(report, api_role["owner"])


def test_migrations_grant_leia_app_exactly_the_required_privileges(scratch):  # noqa: F811
    """The migrations' GRANTs and api_privileges.REQUIRED must never drift apart."""
    import migrate_crm

    with scratch(autocommit=True) as connection:
        created = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'leia_app'").fetchone() is None
        if created:
            connection.execute("CREATE ROLE leia_app NOLOGIN")
    try:
        migrate_crm.migrate()
        with scratch() as connection:
            report = api_privileges.inventory(connection, "leia_app")
        assert {t: set(i["privileges"]) for t, i in report["tables"].items()} == api_privileges.REQUIRED
        if created:  # a pre-existing leia_app may carry attributes this test must not judge
            assert api_privileges.problems(report) == []
    finally:
        with scratch(autocommit=True) as connection:
            connection.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM leia_app")
            if created:
                connection.execute("DROP ROLE leia_app")
