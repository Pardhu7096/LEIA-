"""Regressions for reconcile_migrations.py: history is recorded only when the live
schema is proven identical to what the migrations build, and the development
situation (objects made before tracking, 007 pending) ends at the final schema
with data intact. Needs TEST_DATABASE_URL; skipped otherwise."""

import json
import os
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


def _copy_database(connect):
    """Copy the scratch database (CREATE DATABASE ... TEMPLATE) and return a
    function that points the app settings at the copy."""
    with connect() as connection:
        source = connection.info.dbname
    copy = f"{source}_copy"
    with connect(autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{source}"')
    return source, copy


def _drop(connect, name):
    with connect(autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_status_json_is_structured_and_matches_text(scratch, tmp_path, capsys):
    build_untracked_006(scratch, tracked=[p.name for p in MIGRATIONS[1:7]])
    out = tmp_path / "live.json"
    assert reconcile_migrations.main(["status", "--json", "--output", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["history"] == {"exists": True, "readable": True,
                                 "recorded": [p.name for p in MIGRATIONS[1:7]],
                                 "leia_app_can_read": report["history"]["leia_app_can_read"]}
    assert report["pending"] == ["000_core_baseline.sql", "007_room_panoramas.sql"]
    assert report["matches"][THROUGH] == []
    assert len(report["matches"]["007_room_panoramas.sql"]) == 2
    assert report["property_media"] == {
        "total": 2, "invalid_for_007": 0, "duplicate_room_panoramas": 0}
    assert report["row_counts"]["users"] == 1 and report["row_counts"]["property_media"] == 2
    assert set(report["checksums"]) == set(report["row_counts"]) == set(report["schema"])
    assert all(len(d) == 32 for d in report["checksums"].values())
    capsys.readouterr()
    # The text report says the same thing.
    assert reconcile_migrations.main(["status"]) == 0
    text = capsys.readouterr().out
    assert "pending: 000_core_baseline.sql, 007_room_panoramas.sql" in text
    assert f"  {THROUGH}: yes" in text
    assert "  007_room_panoramas.sql: no (2 difference(s))" in text


def test_compare_status_accepts_a_faithful_copy_and_catches_changes(
        scratch, tmp_path, capsys, monkeypatch):
    build_untracked_006(scratch, tracked=[p.name for p in MIGRATIONS[1:7]])
    source, copy = _copy_database(scratch)
    try:
        live, restored = tmp_path / "live.json", tmp_path / "restored.json"
        assert reconcile_migrations.main(["status", "--json", "--output", str(live)]) == 0
        monkeypatch.setenv("DB_NAME", copy)
        assert reconcile_migrations.main(["status", "--json", "--output", str(restored)]) == 0
        capsys.readouterr()
        assert reconcile_migrations.main(["compare-status", str(live), str(restored)]) == 0
        assert capsys.readouterr().out.strip() == "Identical."

        # Same row count, different content: only the checksum notices.
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], dbname=copy) as connection:
            connection.execute("UPDATE users SET name = 'Changed'")
        assert reconcile_migrations.main(["status", "--json", "--output", str(restored)]) == 0
        capsys.readouterr()
        assert reconcile_migrations.main(["compare-status", str(live), str(restored)]) == 1
        out = capsys.readouterr().out
        assert out.startswith("checksums:") and "1 difference(s)." in out

        # A missing row, history row and index are all reported.
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], dbname=copy) as connection:
            connection.execute("DELETE FROM property_media WHERE kind = 'panorama'")
            connection.execute("DELETE FROM crm_schema_migrations WHERE name = %s", (THROUGH,))
            connection.execute("DROP INDEX request_limits_expiry")
        assert reconcile_migrations.main(["status", "--json", "--output", str(restored)]) == 0
        capsys.readouterr()
        assert reconcile_migrations.main(["compare-status", str(live), str(restored)]) == 1
        out = capsys.readouterr().out
        for field in ("history:", "pending:", "row_counts:", "property_media:",
                      "schema: request_limits.indexes.request_limits_expiry: missing"):
            assert field in out
    finally:
        _drop(scratch, copy)


def test_compare_status_rejects_reports_without_data(tmp_path, capsys):
    """A report written by a role that cannot read history has no checksums."""
    partial = {"connected_as": "leia_app", "database": "leia", "database_owner": "postgres",
               "server_version": "18.6", "table_owners": {},
               "history": {"exists": True, "readable": False, "recorded": None,
                           "leia_app_can_read": None}}
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps(partial), encoding="utf-8")
    second.write_text("﻿" + json.dumps(partial), encoding="utf-8")  # BOM, as PowerShell 5.1 writes
    assert reconcile_migrations.main(["compare-status", str(first), str(second)]) == 1
    assert "incomplete status" in capsys.readouterr().out


def test_status_json_for_a_role_that_cannot_read_history_exits_2(scratch, tmp_path, monkeypatch):
    build_untracked_006(scratch, tracked=[THROUGH])
    role, password = f"api_{uuid4().hex[:12]}", uuid4().hex
    with scratch(autocommit=True) as connection:
        connection.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
    try:
        monkeypatch.setenv("DB_USER", role)
        monkeypatch.setenv("DB_PASSWORD", password)
        out = tmp_path / "app.json"
        assert reconcile_migrations.main(["status", "--json", "--output", str(out)]) == 2
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["history"]["readable"] is False and "row_counts" not in report
    finally:
        with scratch(autocommit=True) as connection:
            connection.execute(f"DROP OWNED BY {role}")
            connection.execute(f"DROP ROLE {role}")
