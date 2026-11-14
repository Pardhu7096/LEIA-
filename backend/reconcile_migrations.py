"""Inspect migration history and, only when proven safe, record migrations that a
database already has (objects created before migration tracking existed).

    python reconcile_migrations.py --owner postgres              # status, read-only
    python reconcile_migrations.py --owner postgres adopt --through 006_request_limits.sql
    python reconcile_migrations.py --owner postgres adopt --through 006_request_limits.sql --apply

`status` changes nothing (read-only session). `adopt` is a dry run unless --apply is
given. It never runs migration SQL: it records 000..THROUGH in crm_schema_migrations
only when the live schema is identical to the schema those migrations build
(tests/expected_schema_<NNN>.json). Any difference, an unknown snapshot or a
history that already disagrees stops it with nothing changed. Later migrations
(for example 007) are then applied by migrate_crm.py as usual.
"""

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path

from psycopg import sql

from database import get_connection
import schema_report

BACKEND = Path(__file__).resolve().parent
MIGRATIONS = sorted((BACKEND / "migrations").glob("*.sql"))
TRACKING = "crm_schema_migrations"  # Constant; the only name interpolated into SQL.
LOCK_ID = 72180411  # Same advisory lock as migrate_crm.py.


def snapshot_for(name: str) -> Path:
    """Expected schema after migration `name`; the last migration uses expected_schema.json."""
    if MIGRATIONS and name == MIGRATIONS[-1].name:
        return BACKEND / "tests" / "expected_schema.json"
    return BACKEND / "tests" / f"expected_schema_{name[:3]}.json"


def live_schema(connection) -> dict:
    connection.execute("SELECT set_config('search_path', 'public', true)")
    return schema_report.describe(connection)


def schema_differences(expected: dict, actual: dict) -> list[str]:
    """Differences, except a tracking table that does not exist yet (adopt creates it)."""
    if TRACKING not in actual:
        expected = {k: v for k, v in expected.items() if k != TRACKING}
    return schema_report.differences(expected, actual)


def recorded(connection) -> list[str] | None:
    if connection.execute(f"SELECT to_regclass('public.{TRACKING}') AS t").fetchone()["t"] is None:
        return None
    return [r["name"] for r in connection.execute(f"SELECT name FROM public.{TRACKING} ORDER BY name")]


def status(connection) -> int:
    connection.read_only = True
    who = connection.execute(
        """SELECT current_user AS connected_as, current_database() AS database,
                  pg_get_userbyid(d.datdba) AS database_owner,
                  current_setting('server_version') AS server_version
           FROM pg_database d WHERE d.datname = current_database()""").fetchone()
    for key, value in who.items():
        print(f"{key}: {value}")
    print("table owners:")
    for row in connection.execute(
            """SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'
               ORDER BY tablename"""):
        print(f"  {row['tablename']}: {row['tableowner']}")

    if connection.execute(f"SELECT to_regclass('public.{TRACKING}') AS t").fetchone()["t"] is None:
        print(f"{TRACKING}: does not exist")
        applied = None
    else:
        readable = connection.execute(
            f"SELECT has_table_privilege('public.{TRACKING}', 'SELECT') AS ok").fetchone()["ok"]
        if not readable:
            print(f"{TRACKING}: exists, but {who['connected_as']} cannot read it "
                  "(rerun with --owner)")
            return 2
        applied = recorded(connection)
        print(f"{TRACKING}: exists; recorded: {', '.join(applied) or '(none)'}")
        if connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = 'leia_app'").fetchone():
            grant = connection.execute(
                f"SELECT has_table_privilege('leia_app', 'public.{TRACKING}', 'SELECT') AS ok"
            ).fetchone()["ok"]
            print(f"  leia_app can read it: {'yes' if grant else 'no'}")
    shipped = [p.name for p in MIGRATIONS]
    print(f"pending: {', '.join(n for n in shipped if n not in (applied or [])) or '(none)'}")
    unknown = sorted(set(applied or []) - set(shipped))
    if unknown:
        print(f"recorded but not shipped: {', '.join(unknown)}")

    schema = live_schema(connection)
    print("schema matches the expected state after:")
    for name in shipped:
        path = snapshot_for(name)
        if path.exists():
            found = schema_differences(json.loads(path.read_text(encoding="utf-8")), schema)
            print(f"  {name}: {'yes' if not found else f'no ({len(found)} difference(s))'}")

    if "property_media" in schema:
        media = connection.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE NOT (
                        (kind IN ('photo', 'panorama_room') AND room IS NOT NULL
                          AND char_length(btrim(room)) BETWEEN 1 AND 60
                          AND (kind <> 'panorama_room' OR NOT is_gallery_cover))
                        OR (kind IN ('cover', 'panorama') AND room IS NULL
                          AND NOT is_gallery_cover))) AS invalid_for_007
               FROM public.property_media""").fetchone()
        duplicates = connection.execute(
            """SELECT count(*) AS n FROM (
                 SELECT 1 FROM public.property_media WHERE kind = 'panorama_room'
                 GROUP BY property_id, lower(room) HAVING count(*) > 1) d""").fetchone()["n"]
        print(f"property_media rows: {media['total']}; incompatible with 007: "
              f"{media['invalid_for_007']}; duplicate room panoramas: {duplicates}")

    print("exact row counts:")
    for table in schema:
        count = connection.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(
            sql.Identifier("public", table))).fetchone()["n"]
        print(f"  {table}: {count}")
    return 0


def adopt(connection, through: str, apply: bool) -> int:
    names = [p.name for p in MIGRATIONS]
    if through not in names:
        print(f"Unknown migration {through!r}. Shipped: {', '.join(names)}")
        return 2
    path = snapshot_for(through)
    if not path.exists():
        print(f"No verified schema snapshot for {through} ({path.name}); refusing.")
        return 2
    wanted = names[: names.index(through) + 1]

    connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
    applied = recorded(connection) or []
    beyond = [n for n in applied if n not in wanted]
    if beyond:
        print(f"History already records later migrations ({', '.join(beyond)}); refusing.")
        return 2
    found = schema_differences(json.loads(path.read_text(encoding="utf-8")), live_schema(connection))
    if found:
        print(f"Live schema is not the state after {through}; nothing recorded:")
        for line in found:
            print(f"  {line}")
        return 1

    missing = [n for n in wanted if n not in applied]
    connection.execute(f"""CREATE TABLE IF NOT EXISTS public.{TRACKING} (
        name TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    connection.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
            GRANT SELECT ON public.{TRACKING} TO leia_app;
        END IF;
    END $$;""")
    for name in missing:
        connection.execute(f"INSERT INTO public.{TRACKING} (name) VALUES (%s)", (name,))
    verb = "Recorded" if apply else "Would record"
    print(f"Schema verified identical to the state after {through}.")
    print(f"{verb}: {', '.join(missing) or '(nothing, history already complete)'}")
    if not apply:
        connection.rollback()
        print("Dry run: nothing changed. Add --apply to record.")
        return 0
    connection.commit()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner", help="Database owner role; prompts privately for its password.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="Read-only report (default).")
    adopt_parser = sub.add_parser("adopt", help="Record already-present migrations.")
    adopt_parser.add_argument("--through", required=True, help="Last migration to record.")
    adopt_parser.add_argument("--apply", action="store_true", help="Commit; default is a dry run.")
    args = parser.parse_args(argv)
    if args.owner:
        os.environ["DB_USER"] = args.owner
        os.environ["DB_PASSWORD"] = getpass("Database owner password: ")
    with get_connection() as connection:
        if args.command == "adopt":
            return adopt(connection, args.through, args.apply)
        return status(connection)


if __name__ == "__main__":
    sys.exit(main())
