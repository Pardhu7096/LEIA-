"""Regressions for reconcile_migrations.py: history is recorded only when the live
schema is proven identical to what the migrations build, and the development
situation (objects made before tracking, 007 pending) ends at the final schema
with data intact. Needs TEST_DATABASE_URL; skipped otherwise."""

import json
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from test_core_schema import BACKEND, EXPECTED, MIGRATIONS, scratch, snapshot  # noqa: F401
import reconcile_migrations  # noqa: E402
import schema_report  # noqa: E402

THROUGH = "006_request_limits.sql"
EXPECTED_006 = json.loads((BACKEND / "tests" / "expected_schema_006.json").read_text())
TRACKING_DDL = """CREATE TABLE public.crm_schema_migrations (
    name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"""


def build_untracked_006(connect, *, tracked=()):
    """Run 000-006 directly, as the development database was built (no history),
    optionally recording some names; add a few rows of data."""
    with connect() as connection:
        for path in MIGRATIONS[:7]:
            connection.execute(path.read_text())
        if tracked:
            connection.execute(TRACKING_DDL)
            for name in tracked:
                connection.execute(
                    "INSERT INTO crm_schema_migrations (name) VALUES (%s)", (name,))
        user = connection.execute(
            "INSERT INTO users (name, email, password_hash, role)"
            " VALUES ('Agent', 'agent@example.com', 'x', 'agent') RETURNING id").fetchone()["id"]
        prop = connection.execute(
            "INSERT INTO properties (created_by, title, property_type) VALUES (%s, 'Flat', 'flat')"
            " RETURNING id", (user,)).fetchone()["id"]
        connection.execute(
            "INSERT INTO property_media (id, property_id, kind, room, content)"
            " VALUES (%s, %s, 'photo', 'Kitchen', '\\x01')", (uuid4(), prop))
        connection.execute(
            "INSERT INTO property_media (id, property_id, kind, content)"
            " VALUES (%s, %s, 'panorama', '\\x01')", (uuid4(), prop))
        return user, prop


def counts(connect):
    with connect() as connection:
        return {t: connection.execute(f'SELECT count(*) AS n FROM "{t}"').fetchone()["n"]
                for t in ("users", "properties", "property_media")}


def history(connect):
    with connect() as connection:
        if connection.execute(
                "SELECT to_regclass('public.crm_schema_migrations') AS t").fetchone()["t"] is None:
            return None
        return [r["name"] for r in connection.execute(
            "SELECT name FROM crm_schema_migrations ORDER BY name")]


def test_snapshot_006_is_what_migrations_000_to_006_build(scratch):
    with scratch() as connection:
        connection.execute(TRACKING_DDL)
        for path in MIGRATIONS[:7]:
            connection.execute(path.read_text())
    assert MIGRATIONS[6].name == THROUGH
    assert schema_report.differences(EXPECTED_006, snapshot(scratch)) == []
    # The 006 state differs from the final schema exactly as development does.
    assert sorted(schema_report.differences(EXPECTED, EXPECTED_006)) == sorted(
        schema_report.differences(EXPECTED, snapshot(scratch)))
    assert len(schema_report.differences(EXPECTED, EXPECTED_006)) == 2


def test_untracked_database_is_adopted_then_007_applied(scratch, capsys):
    import migrate_crm

    build_untracked_006(scratch)
    before = counts(scratch)

    assert reconcile_migrations.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "crm_schema_migrations: does not exist" in out
    assert f"  {THROUGH}: yes" in out
    assert "incompatible with 007: 0; duplicate room panoramas: 0" in out

    assert reconcile_migrations.main(["adopt", "--through", THROUGH]) == 0
    assert "Dry run: nothing changed." in capsys.readouterr().out
    assert history(scratch) is None

    assert reconcile_migrations.main(["adopt", "--through", THROUGH, "--apply"]) == 0
    assert history(scratch) == [p.name for p in MIGRATIONS[:7]]
    # Idempotent: a second run records nothing new.
    assert reconcile_migrations.main(["adopt", "--through", THROUGH, "--apply"]) == 0
    assert "history already complete" in capsys.readouterr().out

    migrate_crm.migrate()
    assert capsys.readouterr().out.splitlines()[0] == "Applied 007_room_panoramas.sql"
    assert schema_report.differences(EXPECTED, snapshot(scratch)) == []
    assert counts(scratch) == before


def test_partial_history_is_completed(scratch):
    build_untracked_006(scratch, tracked=[p.name for p in MIGRATIONS[1:7]])  # 001-006, no 000
    assert reconcile_migrations.main(["adopt", "--through", THROUGH, "--apply"]) == 0
    assert history(scratch) == [p.name for p in MIGRATIONS[:7]]


@pytest.mark.parametrize("drift", [
    "DROP INDEX request_limits_expiry",
    "ALTER TABLE properties DROP COLUMN water_notes",
    "ALTER TABLE property_media DROP CONSTRAINT property_media_kind_check",
    "CREATE TABLE stray (id int)",
])
def test_any_schema_difference_refuses_and_changes_nothing(scratch, capsys, drift):
    build_untracked_006(scratch)
    with scratch() as connection:
        connection.execute(drift)
    assert reconcile_migrations.main(["adopt", "--through", THROUGH, "--apply"]) == 1
    assert "nothing recorded" in capsys.readouterr().out
    assert history(scratch) is None


def test_refuses_without_snapshot_unknown_name_or_later_history(scratch, capsys):
    build_untracked_006(scratch, tracked=["007_room_panoramas.sql"])
    assert reconcile_migrations.main(["adopt", "--through", "003_property_media.sql"]) == 2
    assert "No verified schema snapshot" in capsys.readouterr().out
    assert reconcile_migrations.main(["adopt", "--through", "999_nope.sql"]) == 2
    assert reconcile_migrations.main(["adopt", "--through", THROUGH, "--apply"]) == 2
    assert "records later migrations" in capsys.readouterr().out
    assert history(scratch) == ["007_room_panoramas.sql"]


def test_status_explains_unreadable_history_for_the_api_role(scratch, capsys, monkeypatch):
    """The suspected development situation: history exists but leia_app-like roles
    were never granted SELECT on it, so they cannot see it."""
    build_untracked_006(scratch, tracked=[p.name for p in MIGRATIONS[1:7]])
    role, password = f"api_{uuid4().hex[:12]}", uuid4().hex
    with scratch(autocommit=True) as connection:
        connection.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        connection.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    try:
        monkeypatch.setenv("DB_USER", role)
        monkeypatch.setenv("DB_PASSWORD", password)
        assert reconcile_migrations.main(["status"]) == 2
        out = capsys.readouterr().out
        assert f"exists, but {role} cannot read it (rerun with --owner)" in out
        with scratch() as connection:
            # information_schema hides it from that role; the catalog does not.
            target = conninfo_to_dict(connection.info.dsn)
        with psycopg.connect(dbname=target["dbname"], host=target.get("host"),
                             port=target.get("port"), user=role, password=password) as api:
            hidden = api.execute(
                """SELECT count(*) FROM information_schema.tables
                   WHERE table_name = 'crm_schema_migrations'""").fetchone()[0]
            catalog = api.execute(
                "SELECT to_regclass('public.crm_schema_migrations') IS NOT NULL").fetchone()[0]
        assert (hidden, catalog) == (0, True)
    finally:
        with scratch(autocommit=True) as connection:
            connection.execute(f"DROP OWNED BY {role}")
            connection.execute(f"DROP ROLE {role}")


def test_status_is_read_only(scratch, capsys):
    build_untracked_006(scratch)
    assert reconcile_migrations.main(["status"]) == 0
    assert history(scratch) is None  # status never creates the tracking table
