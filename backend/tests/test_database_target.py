"""Step-4 safeguards: nothing meant for the scratch copy can reach the live database.

Two databases play the roles of the development database ("live", 006 state, 007
pending) and the scratch copy. The tests reproduce each way the target could go
wrong - DB_NAME pointing at live, DB_NAME missing so backend/.env supplies live,
DB_NAME empty, the expectation omitted - and show that the runner and the rollback
refuse on the very connection that would write, leaving live unchanged. Needs
TEST_DATABASE_URL."""

import ast
import os
from uuid import uuid4

import psycopg
import pytest
from dotenv import load_dotenv

from test_core_schema import BACKEND, MIGRATIONS, scratch  # noqa: F401
import migrate_crm  # noqa: E402
import reconcile_migrations  # noqa: E402

M007 = "007_room_panoramas.sql"


def build_006(connection):
    connection.execute("""CREATE TABLE public.crm_schema_migrations (
        name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    for path in MIGRATIONS[:7]:
        connection.execute(path.read_text())
        connection.execute("INSERT INTO crm_schema_migrations (name) VALUES (%s)", (path.name,))


@pytest.fixture
def pair(scratch, monkeypatch, tmp_path):  # noqa: F811
    """`live` = the scratch fixture database; `copy` = a second database. Both 006."""
    with scratch() as connection:
        live = connection.info.dbname
        build_006(connection)
    copy = f"{live}_copy"
    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{live}"')

    def snapshot(name):
        monkeypatch.setenv("DB_NAME", name)
        path = tmp_path / f"{name}-{uuid4().hex}.json"
        assert reconcile_migrations.main(["status", "--json", "--output", str(path)]) == 0
        return path

    def recorded(name):
        with psycopg.connect(url, dbname=name) as connection:
            return [r[0] for r in connection.execute(
                "SELECT name FROM crm_schema_migrations ORDER BY name")]

    try:
        yield {"live": live, "copy": copy, "snapshot": snapshot, "recorded": recorded}
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')


def unchanged(pair, before, capsys):
    after = pair["snapshot"](pair["live"])
    capsys.readouterr()
    assert reconcile_migrations.main(["compare-status", str(before), str(after)]) == 0
    assert M007 not in pair["recorded"](pair["live"])


def test_wrong_db_name_cannot_apply_007_to_live(pair, monkeypatch, capsys):
    before = pair["snapshot"](pair["live"])
    monkeypatch.setenv("DB_NAME", pair["live"])          # the mistake
    capsys.readouterr()
    assert migrate_crm.migrate(expect=[M007], expect_database=pair["copy"]) == 2
    out = capsys.readouterr().out
    assert f"Expected database '{pair['copy']}', connected to '{pair['live']}'; refusing" in out
    assert "Applied" not in out
    unchanged(pair, before, capsys)


def test_missing_db_name_falls_back_to_env_file_and_is_refused(pair, monkeypatch, capsys, tmp_path):
    """If a script forgot to set DB_NAME, backend/.env (DB_NAME=live) fills it in."""
    before = pair["snapshot"](pair["live"])
    monkeypatch.delenv("DB_NAME")
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_NAME={pair['live']}\n", encoding="utf-8")
    load_dotenv(env_file)                                 # what database.py does at import
    assert os.environ["DB_NAME"] == pair["live"]
    capsys.readouterr()
    assert migrate_crm.migrate(expect=[M007], expect_database=pair["copy"]) == 2
    assert "refusing" in capsys.readouterr().out
    unchanged(pair, before, capsys)


def test_environment_variable_wins_over_env_file(pair, monkeypatch, capsys, tmp_path):
    """The precedence the scripts rely on: a set DB_NAME is not replaced by .env."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_NAME={pair['live']}\n", encoding="utf-8")
    monkeypatch.setenv("DB_NAME", pair["copy"])
    load_dotenv(env_file)
    assert os.environ["DB_NAME"] == pair["copy"]
    before_live = pair["snapshot"](pair["live"])
    monkeypatch.setenv("DB_NAME", pair["copy"])
    capsys.readouterr()
    assert migrate_crm.migrate(expect=[M007], expect_database=pair["copy"]) == 0
    assert f"Connected to database: {pair['copy']}" in capsys.readouterr().out
    assert M007 in pair["recorded"](pair["copy"])
    unchanged(pair, before_live, capsys)


def test_database_py_never_lets_env_file_override_the_environment():
    """database.py must call load_dotenv without override=True (env vars win)."""
    tree = ast.parse((BACKEND / "database.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) == "load_dotenv"]
    assert len(calls) == 1
    assert not [k for k in calls[0].keywords if k.arg == "override"]


def test_empty_db_name_is_refused(pair, monkeypatch, capsys):
    """An empty DB_NAME makes the driver fall back to another database."""
    before = pair["snapshot"](pair["live"])
    monkeypatch.setenv("DB_NAME", "")
    capsys.readouterr()
    assert migrate_crm.migrate(expect=[M007], expect_database=pair["copy"]) == 2
    assert "refusing" in capsys.readouterr().out
    unchanged(pair, before, capsys)


def test_dry_run_checks_the_database_too(pair, monkeypatch, capsys):
    monkeypatch.setenv("DB_NAME", pair["copy"])
    assert migrate_crm.migrate(dry_run=True, expect=[M007], expect_database=pair["live"]) == 2
    monkeypatch.setenv("DB_NAME", pair["live"])
    capsys.readouterr()
    assert migrate_crm.migrate(dry_run=True, expect=[M007], expect_database=pair["live"]) == 0
    assert f"Connected to database: {pair['live']}" in capsys.readouterr().out


def test_cli_refuses_writes_without_expect_database(pair, monkeypatch, capsys):
    before = pair["snapshot"](pair["live"])
    monkeypatch.setenv("DB_NAME", pair["live"])
    capsys.readouterr()
    assert migrate_crm.main(["--expect", M007]) == 2
    assert migrate_crm.main(["--rollback", M007]) == 2
    assert "need --expect-database" in capsys.readouterr().out
    unchanged(pair, before, capsys)
    assert migrate_crm.main(["--dry-run", "--expect", M007]) == 0  # read-only is allowed


def test_rollback_runs_only_on_the_expected_database(pair, monkeypatch, capsys):
    for name in (pair["live"], pair["copy"]):            # both at 007
        monkeypatch.setenv("DB_NAME", name)
        assert migrate_crm.migrate(expect=[M007], expect_database=name) == 0
    before = pair["snapshot"](pair["live"])
    monkeypatch.setenv("DB_NAME", pair["live"])          # the mistake
    capsys.readouterr()
    assert migrate_crm.main(["--expect-database", pair["copy"], "--rollback", M007]) == 2
    assert "refusing" in capsys.readouterr().out
    after = pair["snapshot"](pair["live"])
    assert reconcile_migrations.main(["compare-status", str(before), str(after)]) == 0
    assert M007 in pair["recorded"](pair["live"])

    monkeypatch.setenv("DB_NAME", pair["copy"])
    assert migrate_crm.main(["--expect-database", pair["copy"], "--rollback", M007]) == 0
    assert M007 not in pair["recorded"](pair["copy"])
    assert M007 in pair["recorded"](pair["live"])


def test_rollback_refuses_unknown_names_and_paths(pair, monkeypatch, capsys):
    monkeypatch.setenv("DB_NAME", pair["copy"])
    for name in ("999_missing.sql", "../migrations/007_room_panoramas.sql", "/etc/passwd"):
        assert migrate_crm.rollback(name, pair["copy"]) == 2
    assert migrate_crm.rollback(M007, "") == 2
