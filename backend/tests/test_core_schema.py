"""Regressions for the core schema built by the migrations (000_core_baseline.sql
and later): it must equal the development database's schema, its CHECK
constraints and indexes must exist and work, and an existing development-shaped
database must be adopted without any change. Needs TEST_DATABASE_URL and a role
that may create scratch databases; skipped otherwise."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import schema_report  # noqa: E402

EXPECTED = json.loads((BACKEND / "tests" / "expected_schema.json").read_text())
DEVELOPMENT_TABLES = BACKEND / "tests" / "fixtures" / "development_core_tables.sql"
MIGRATIONS = sorted((BACKEND / "migrations").glob("*.sql"))


@pytest.fixture
def scratch(monkeypatch):
    """Connect function for an empty scratch database, also set as the app's DB_*."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated disposable database.")
    name = f"leia_schema_{uuid4().hex}"
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
    import migrate_crm
    import database

    # Undo any substitution another test module left on the migration runner.
    monkeypatch.setattr(migrate_crm, "get_connection", database.get_connection)

    def connect(**options):
        return psycopg.connect(url, dbname=name, row_factory=dict_row, **options)

    try:
        yield connect
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def snapshot(connect) -> dict:
    with connect() as connection:
        connection.execute("SELECT set_config('search_path', 'public', true)")
        return schema_report.describe(connection)


def test_empty_database_migrates_to_the_development_schema(scratch):
    import migrate_crm

    migrate_crm.migrate()
    migrate_crm.migrate()  # Tracked rerun is harmless.
    schema = snapshot(scratch)
    assert schema_report.differences(EXPECTED, schema) == []

    # The six points found when comparing with the development database.
    assert schema["users"]["constraints"]["email_lowercase"] == (
        "CHECK (((email)::text = lower((email)::text)))")
    assert schema["auth_sessions"]["constraints"]["session_expiry_valid"] == (
        "CHECK ((expires_at > created_at))")
    assert schema["auth_sessions"]["indexes"]["auth_sessions_user_id_idx"] == (
        "CREATE INDEX auth_sessions_user_id_idx ON public.auth_sessions USING btree (user_id)")
    assert schema["chat_messages"]["indexes"]["chat_messages_conversation_idx"] == (
        "CREATE INDEX chat_messages_conversation_idx "
        "ON public.chat_messages USING btree (conversation_id, id)")
    assert schema["conversations"]["indexes"]["conversations_user_updated_idx"] == (
        "CREATE INDEX conversations_user_updated_idx "
        "ON public.conversations USING btree (user_id, updated_at DESC)")
    assert schema["conversations"]["columns"]["title"]["type"] == "text"
    assert schema["conversations"]["columns"]["id"]["default"] is None
    assert schema["chat_messages"]["columns"]["property_id"] == {
        "type": "uuid", "nullable": True, "default": None, "identity": None}


def test_core_constraints_reject_invalid_rows(scratch):
    import migrate_crm

    migrate_crm.migrate()
    with scratch() as connection:
        user_id = connection.execute(
            "INSERT INTO users (name, email, password_hash) VALUES ('A', 'a@example.com', 'x')"
            " RETURNING id").fetchone()["id"]

    def violation(sql, params=()):
        with pytest.raises(psycopg.errors.IntegrityError) as raised:
            with scratch() as connection:
                connection.execute(sql, params)
        return raised.value

    error = violation(
        "INSERT INTO users (name, email, password_hash) VALUES ('B', 'B@Example.com', 'x')")
    assert error.diag.constraint_name == "email_lowercase"
    error = violation("UPDATE users SET email = 'A@example.com' WHERE id = %s", (user_id,))
    assert error.diag.constraint_name == "email_lowercase"

    error = violation(
        "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at)"
        " VALUES (%s, 'h1', CURRENT_TIMESTAMP)", (user_id,))
    assert error.diag.constraint_name == "session_expiry_valid"
    error = violation(
        "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at)"
        " VALUES (%s, 'h2', CURRENT_TIMESTAMP - interval '1 day')", (user_id,))
    assert error.diag.constraint_name == "session_expiry_valid"

    # Conversations need an id from the caller, as the API always gives one.
    error = violation("INSERT INTO conversations (user_id) VALUES (%s)", (user_id,))
    assert isinstance(error, psycopg.errors.NotNullViolation)

    # What the API writes is accepted, including titles longer than 200 characters.
    with scratch() as connection:
        connection.execute(
            "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at)"
            " VALUES (%s, 'ok', %s)",
            (user_id, datetime.now(timezone.utc) + timedelta(days=7)))
        connection.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)",
            (uuid4(), user_id, "t" * 500))


def test_api_queries_can_use_the_core_indexes(scratch):
    import migrate_crm

    migrate_crm.migrate()
    with scratch() as connection:
        connection.execute("SET enable_seqscan = off")
        plans = {
            "conversations_user_updated_idx": """
                SELECT id, title, updated_at FROM conversations WHERE user_id = %s
                ORDER BY updated_at DESC, id DESC LIMIT 100""",
            "chat_messages_conversation_idx": """
                SELECT id, role, content, properties, property_id FROM chat_messages
                WHERE conversation_id = %s ORDER BY id""",
            "auth_sessions_user_id_idx": "SELECT id FROM auth_sessions WHERE user_id = %s",
        }
        for index, query in plans.items():
            plan = connection.execute(
                f"EXPLAIN (FORMAT JSON) {query}", (uuid4(),)).fetchone()["QUERY PLAN"]
            assert index in json.dumps(plan), (index, plan)


def test_development_database_is_adopted_without_change(scratch):
    """A database whose core tables were made by hand and which already has
    001-007 (the development database) only records 000; nothing is altered."""
    import migrate_crm

    with scratch() as connection:
        connection.execute(DEVELOPMENT_TABLES.read_text())
        connection.execute("""CREATE TABLE public.crm_schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        for path in MIGRATIONS[1:]:
            connection.execute(path.read_text())
            connection.execute(
                "INSERT INTO crm_schema_migrations (name) VALUES (%s)", (path.name,))
        user_id = connection.execute(
            "INSERT INTO users (name, email, password_hash) VALUES ('Dev', 'dev@example.com', 'x')"
            " RETURNING id").fetchone()["id"]
        conversation_id = uuid4()
        connection.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)",
            (conversation_id, user_id, "x" * 300))
        connection.execute(
            "INSERT INTO chat_messages (conversation_id, role, content) VALUES (%s, 'user', 'hi')",
            (conversation_id,))
        rows_before = connection.execute(
            "SELECT c.title, m.content, m.property_id FROM conversations c"
            " JOIN chat_messages m ON m.conversation_id = c.id").fetchall()
        relfiles_before = connection.execute(
            "SELECT relname, relfilenode FROM pg_class WHERE relname = ANY(%s) ORDER BY relname",
            (["users", "auth_sessions", "conversations", "chat_messages"],)).fetchall()
    before = snapshot(scratch)
    assert schema_report.differences(EXPECTED, before) == []

    migrate_crm.migrate()

    assert snapshot(scratch) == before
    with scratch() as connection:
        assert [row["name"] for row in connection.execute(
            "SELECT name FROM crm_schema_migrations ORDER BY name")] == [p.name for p in MIGRATIONS]
        assert connection.execute(
            "SELECT c.title, m.content, m.property_id FROM conversations c"
            " JOIN chat_messages m ON m.conversation_id = c.id").fetchall() == rows_before
        # Same storage files: no table was rewritten.
        assert connection.execute(
            "SELECT relname, relfilenode FROM pg_class WHERE relname = ANY(%s) ORDER BY relname",
            (["users", "auth_sessions", "conversations", "chat_messages"],)).fetchall() == (
            relfiles_before)


def test_schema_report_compare_flags_drift(scratch, capsys):
    import migrate_crm

    migrate_crm.migrate()
    capsys.readouterr()  # Discard the migration runner's own output.
    expected = str(BACKEND / "tests" / "expected_schema.json")
    assert schema_report.main(["--compare", expected]) == 0
    assert capsys.readouterr().out.strip() == "Schema matches."

    with scratch() as connection:
        connection.execute("DROP INDEX conversations_user_updated_idx")
        connection.execute("ALTER TABLE users DROP CONSTRAINT email_lowercase")
        connection.execute("ALTER TABLE conversations ALTER COLUMN title TYPE VARCHAR(200)")
    assert schema_report.main(["--compare", expected]) == 1
    out = capsys.readouterr().out
    assert "conversations.indexes.conversations_user_updated_idx: missing" in out
    assert "users.constraints.email_lowercase: missing" in out
    assert "conversations.columns.title: expected" in out
    assert "3 difference(s)." in out


def test_schema_report_session_is_read_only(scratch, monkeypatch):
    def try_to_write(connection):
        connection.execute("CREATE TABLE should_not_exist (id int)")
        return {}

    monkeypatch.setattr(schema_report, "describe", try_to_write)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        schema_report.main([])
    with scratch() as connection:
        assert connection.execute(
            "SELECT to_regclass('public.should_not_exist') AS t").fetchone()["t"] is None
