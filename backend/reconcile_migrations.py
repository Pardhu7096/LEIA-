"""Inspect migration history and, only when proven safe, record migrations that a
database already has (objects created before migration tracking existed).

    python reconcile_migrations.py --owner postgres              # status, read-only
    python reconcile_migrations.py --owner postgres status --json --output live.json
    python reconcile_migrations.py compare-status live.json restored.json
        [--allow-database-owner-difference leia_app:postgres]
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


def collect_status(connection, checksums: bool = False) -> tuple[dict, int]:
    """Everything `status` reports, as data. Read-only session. Exit code 2 when the
    connected role cannot read the migration history (the rest is then omitted)."""
    connection.read_only = True
    report = dict(connection.execute(
        """SELECT current_user AS connected_as, current_database() AS database,
                  pg_get_userbyid(d.datdba) AS database_owner,
                  current_setting('server_version') AS server_version
           FROM pg_database d WHERE d.datname = current_database()""").fetchone())
    report["table_owners"] = {
        row["tablename"]: row["tableowner"] for row in connection.execute(
            """SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'
               ORDER BY tablename""")}
    history = {"exists": False, "readable": None, "recorded": None, "leia_app_can_read": None}
    report["history"] = history
    if connection.execute(f"SELECT to_regclass('public.{TRACKING}') AS t").fetchone()["t"] is not None:
        history["exists"] = True
        history["readable"] = connection.execute(
            f"SELECT has_table_privilege('public.{TRACKING}', 'SELECT') AS ok").fetchone()["ok"]
        if not history["readable"]:
            return report, 2
        history["recorded"] = recorded(connection)
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'leia_app'").fetchone():
            history["leia_app_can_read"] = connection.execute(
                f"SELECT has_table_privilege('leia_app', 'public.{TRACKING}', 'SELECT') AS ok"
            ).fetchone()["ok"]
    applied = history["recorded"] or []
    shipped = [p.name for p in MIGRATIONS]
    report["pending"] = [n for n in shipped if n not in applied]
    report["recorded_not_shipped"] = sorted(set(applied) - set(shipped))

    schema = live_schema(connection)
    report["schema"] = schema
    report["matches"] = {}
    for name in shipped:
        path = snapshot_for(name)
        if path.exists():
            report["matches"][name] = schema_differences(
                json.loads(path.read_text(encoding="utf-8")), schema)

    report["property_media"] = None
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
        report["property_media"] = {"total": media["total"],
                                    "invalid_for_007": media["invalid_for_007"],
                                    "duplicate_room_panoramas": duplicates}

    report["row_counts"] = {}
    report["checksums"] = {} if checksums else None
    for table in schema:
        name = sql.Identifier("public", table)
        report["row_counts"][table] = connection.execute(
            sql.SQL("SELECT count(*) AS n FROM {}").format(name)).fetchone()["n"]
        if checksums:
            # Order-independent digest of every row's full text form.
            report["checksums"][table] = connection.execute(sql.SQL(
                "SELECT md5(coalesce(string_agg(h, '' ORDER BY h), '')) AS d "
                "FROM (SELECT md5(t::text) AS h FROM {} t) rows").format(name)).fetchone()["d"]
    return report, 0


def print_status(report: dict) -> None:
    for key in ("connected_as", "database", "database_owner", "server_version"):
        print(f"{key}: {report[key]}")
    print("table owners:")
    for table, owner in report["table_owners"].items():
        print(f"  {table}: {owner}")
    history = report["history"]
    if not history["exists"]:
        print(f"{TRACKING}: does not exist")
    elif not history["readable"]:
        print(f"{TRACKING}: exists, but {report['connected_as']} cannot read it "
              "(rerun with --owner)")
        return
    else:
        print(f"{TRACKING}: exists; recorded: {', '.join(history['recorded']) or '(none)'}")
        if history["leia_app_can_read"] is not None:
            print(f"  leia_app can read it: {'yes' if history['leia_app_can_read'] else 'no'}")
    print(f"pending: {', '.join(report['pending']) or '(none)'}")
    if report["recorded_not_shipped"]:
        print(f"recorded but not shipped: {', '.join(report['recorded_not_shipped'])}")
    print("schema matches the expected state after:")
    for name, found in report["matches"].items():
        print(f"  {name}: {'yes' if not found else f'no ({len(found)} difference(s))'}")
    media = report["property_media"]
    if media is not None:
        print(f"property_media rows: {media['total']}; incompatible with 007: "
              f"{media['invalid_for_007']}; duplicate room panoramas: "
              f"{media['duplicate_room_panoramas']}")
    print("exact row counts:")
    for table, count in report["row_counts"].items():
        print(f"  {table}: {count}")


def status(connection, as_json: bool = False, output: Path | None = None) -> int:
    report, code = collect_status(connection, checksums=as_json)
    if as_json:
        text = json.dumps(report, indent=2, sort_keys=True, default=str)
        if output:
            output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    else:
        print_status(report)
    return code


# Fields that must be identical between a database and a restored copy of it.
COMPARED_FIELDS = ("database_owner", "server_version", "table_owners", "history", "pending",
                   "recorded_not_shipped", "matches", "property_media", "row_counts",
                   "checksums", "schema")


def compare_status(first: Path, second: Path,
                   database_owners: tuple[str, str] | None = None) -> int:
    """Compare two `status --json` files field by field (the database name may differ).

    `database_owners` = (first_owner, second_owner) accepts exactly that difference in
    the owner of the database itself, which pg_dump without -C does not carry. Table
    owners and every other field are still compared."""
    a = json.loads(first.read_text(encoding="utf-8-sig"))
    b = json.loads(second.read_text(encoding="utf-8-sig"))
    problems = []
    notes = []
    if database_owners is not None:
        actual = (a.get("database_owner"), b.get("database_owner"))
        if actual == tuple(database_owners):
            notes.append(f"database_owner: {actual[0]} -> {actual[1]} accepted as declared")
            a = {**a, "database_owner": None}
            b = {**b, "database_owner": None}
        else:
            problems.append(f"database_owner: expected {database_owners[0]!r} -> "
                            f"{database_owners[1]!r}, found {actual[0]!r} -> {actual[1]!r}")
            a = {**a, "database_owner": None}
            b = {**b, "database_owner": None}
    for report, path in ((a, first), (b, second)):
        if report.get("checksums") is None or report.get("schema") is None:
            problems.append(f"{path.name}: incomplete status (run as the owner with --json)")
    for field in COMPARED_FIELDS:
        if field == "schema":
            if a.get("schema") is not None and b.get("schema") is not None:
                problems += [f"schema: {line}" for line in
                             schema_report.differences(a["schema"], b["schema"])]
        elif a.get(field) != b.get(field):
            problems.append(f"{field}: {a.get(field)!r} != {b.get(field)!r}")
    for line in notes + problems:
        print(line)
    print("Identical." if not problems else f"{len(problems)} difference(s).")
    return 1 if problems else 0


def adopt(connection, through: str, apply: bool, expect: list[str] | None = None) -> int:
    """Record 000..THROUGH when the live schema equals their snapshot.

    The statements are built once; a dry run (read-only session) prints them and
    --apply executes exactly those, in one transaction under the runner's lock.
    `expect` (the migration names the reviewer expects to be recorded) makes the run
    refuse if the database has changed since the review."""
    names = [p.name for p in MIGRATIONS]
    if through not in names:
        print(f"Unknown migration {through!r}. Shipped: {', '.join(names)}")
        return 2
    path = snapshot_for(through)
    if not path.exists():
        print(f"No verified schema snapshot for {through} ({path.name}); refusing.")
        return 2
    wanted = names[: names.index(through) + 1]

    if not apply:
        connection.read_only = True  # A dry run cannot write, even by mistake.
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
    tracking_exists = connection.execute(
        f"SELECT to_regclass('public.{TRACKING}') IS NOT NULL AS t").fetchone()["t"]
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
    if expect is not None and sorted(expect) != sorted(missing):
        print(f"Expected to record {', '.join(sorted(expect)) or '(nothing)'}, but the database "
              f"needs {', '.join(missing) or '(nothing)'}; refusing. Nothing changed.")
        return 2
    app_role = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = 'leia_app'").fetchone() is not None
    app_reads = app_role and tracking_exists and connection.execute(
        f"SELECT has_table_privilege('leia_app', 'public.{TRACKING}', 'SELECT') AS ok"
    ).fetchone()["ok"]

    # (shown, executed, parameters): the same list is printed and executed.
    statements = []
    if not tracking_exists:
        ddl = (f"CREATE TABLE public.{TRACKING} (name TEXT PRIMARY KEY, "
               "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP);")
        statements.append((ddl, ddl, None))
    if app_role and not app_reads:
        grant = f"GRANT SELECT ON public.{TRACKING} TO leia_app;"
        statements.append((grant, grant, None))
    for name in missing:
        statements.append((f"INSERT INTO public.{TRACKING} (name) VALUES ('{name}');",
                           f"INSERT INTO public.{TRACKING} (name) VALUES (%s);", (name,)))

    print(f"Schema verified identical to the state after {through}.")
    print(f"Already recorded: {', '.join(applied) or '(none)'}")
    if not apply:
        print("Dry run (read-only session). These statements would run in one transaction:")
        for shown, _, _ in statements:
            print(f"  {shown}")
        print(f"Would record: {', '.join(missing) or '(nothing, history already complete)'}")
        print("Dry run: nothing changed. Add --apply to record.")
        connection.rollback()
        return 0

    print("Executing in one transaction:")
    for shown, executed, parameters in statements:
        connection.execute(executed, parameters)
        print(f"  {shown}")
    connection.commit()
    print(f"Committed. Recorded: {', '.join(missing) or '(nothing, history already complete)'}")
    return 0


def verify_adoption(before_path: Path, after_path: Path, added: list[str]) -> int:
    """Check that the only change between two status reports is the recorded history
    (exactly `added`, once each), the API role's read access to it, and that history
    table's row count and checksum. Exit 1 on anything else."""
    before = json.loads(before_path.read_text(encoding="utf-8-sig"))
    after = json.loads(after_path.read_text(encoding="utf-8-sig"))
    results = []

    def check(label, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}{': ' + detail if detail and not ok else ''}")

    for report, path in ((before, before_path), (after, after_path)):
        if report.get("checksums") is None or report.get("schema") is None:
            check(f"{path.name} is a complete owner report", False, "run status --json as the owner")
            return 1
    old, new = before["history"]["recorded"] or [], after["history"]["recorded"] or []
    check("history = before + added", sorted(new) == sorted(old + added),
          f"{new} vs {sorted(old + added)}")
    check("every migration recorded exactly once", len(new) == len(set(new)), str(new))
    check("added migrations were not recorded before", not set(added) & set(old), str(old))
    check("pending = before - added",
          after["pending"] == [n for n in before["pending"] if n not in added],
          f"{after['pending']}")
    check("leia_app can read the history",
          after["history"]["leia_app_can_read"] in (True, None),
          str(after["history"]["leia_app_can_read"]))
    differences = schema_report.differences(before["schema"], after["schema"])
    check("schema unchanged", not differences, "; ".join(differences))
    for field in ("database_owner", "table_owners", "matches", "property_media",
                  "recorded_not_shipped"):
        check(f"{field} unchanged", before.get(field) == after.get(field),
              f"{before.get(field)!r} -> {after.get(field)!r}")
    expected_counts = dict(before["row_counts"])
    expected_counts[TRACKING] = expected_counts.get(TRACKING, 0) + len(added)
    check("row counts unchanged except history (+%d)" % len(added),
          after["row_counts"] == expected_counts,
          f"{after['row_counts']} vs {expected_counts}")
    other = {t: d for t, d in before["checksums"].items() if t != TRACKING}
    check("content checksums unchanged for every other table",
          {t: d for t, d in after["checksums"].items() if t != TRACKING} == other)
    print("VERIFIED." if all(results) else f"{results.count(False)} check(s) failed.")
    return 0 if all(results) else 1


def verify_migration(before_path: Path, after_path: Path, name: str) -> int:
    """Check that the only change between two owner status reports is migration
    `name`: recorded once, the schema moved exactly from the snapshot before it to
    the snapshot after it, and every table's data and ownership is unchanged
    (apart from the one new history row). Exit 1 on anything else."""
    before = json.loads(before_path.read_text(encoding="utf-8-sig"))
    after = json.loads(after_path.read_text(encoding="utf-8-sig"))
    names = [p.name for p in MIGRATIONS]
    results = []

    def check(label, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}{': ' + detail if detail and not ok else ''}")

    for report, path in ((before, before_path), (after, after_path)):
        if report.get("checksums") is None or report.get("schema") is None:
            check(f"{path.name} is a complete owner report", False, "run status --json as the owner")
            return 1
    if name not in names or names.index(name) == 0:
        check(f"{name} is a shipped migration after the first", False)
        return 1
    previous = names[names.index(name) - 1]
    old, new = before["history"]["recorded"] or [], after["history"]["recorded"] or []
    check("history = before + migration", sorted(new) == sorted(old + [name]), f"{new}")
    check("every migration recorded exactly once", len(new) == len(set(new)), str(new))
    check("pending = before - migration",
          after["pending"] == [n for n in before["pending"] if n != name], str(after["pending"]))
    check(f"before: schema was exactly the state after {previous}",
          before["matches"].get(previous) == [], str(before["matches"].get(previous)))
    check(f"after: schema is exactly the state after {name}",
          after["matches"].get(name) == [], str(after["matches"].get(name)))
    want = schema_report.differences(json.loads(snapshot_for(previous).read_text(encoding="utf-8")),
                                     json.loads(snapshot_for(name).read_text(encoding="utf-8")))
    got = schema_report.differences(before["schema"], after["schema"])
    check(f"schema changes are exactly those of {name} ({len(want)})", got == want,
          "; ".join(got))
    for field in ("database_owner", "table_owners", "recorded_not_shipped"):
        check(f"{field} unchanged", before.get(field) == after.get(field),
              f"{before.get(field)!r} -> {after.get(field)!r}")
    expected_counts = dict(before["row_counts"])
    expected_counts[TRACKING] = expected_counts.get(TRACKING, 0) + 1
    check("row counts unchanged except history (+1)", after["row_counts"] == expected_counts,
          f"{after['row_counts']} vs {expected_counts}")
    other = {t: d for t, d in before["checksums"].items() if t != TRACKING}
    check("content checksums unchanged for every other table",
          {t: d for t, d in after["checksums"].items() if t != TRACKING} == other)
    media = after.get("property_media") or {}
    check("no media row violates the new rule", media.get("invalid_for_007") == 0, str(media))
    print("VERIFIED." if all(results) else f"{results.count(False)} check(s) failed.")
    return 0 if all(results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner", help="Database owner role; prompts privately for its password.")
    sub = parser.add_subparsers(dest="command")
    status_parser = sub.add_parser("status", help="Read-only report (default).")
    status_parser.add_argument("--json", action="store_true",
                               help="Machine-readable report, including per-table checksums.")
    status_parser.add_argument("--output", type=Path, help="Write the JSON report to this file.")
    adopt_parser = sub.add_parser("adopt", help="Record already-present migrations.")
    adopt_parser.add_argument("--through", required=True, help="Last migration to record.")
    adopt_parser.add_argument("--apply", action="store_true", help="Commit; default is a dry run.")
    adopt_parser.add_argument("--expect-record", action="append", metavar="NAME",
                              help="Refuse unless exactly these migrations would be recorded.")
    verify_parser = sub.add_parser(
        "verify-adoption", help="Compare status reports before and after adopt; no connection.")
    verify_parser.add_argument("before", type=Path)
    verify_parser.add_argument("after", type=Path)
    verify_parser.add_argument("--added", action="append", required=True, metavar="NAME")
    migration_parser = sub.add_parser(
        "verify-migration", help="Compare status reports before and after one migration.")
    migration_parser.add_argument("before", type=Path)
    migration_parser.add_argument("after", type=Path)
    migration_parser.add_argument("--applied", required=True, metavar="NAME")
    compare_parser = sub.add_parser(
        "compare-status", help="Compare two status --json files; no database connection.")
    compare_parser.add_argument("first", type=Path)
    compare_parser.add_argument("second", type=Path)
    compare_parser.add_argument(
        "--allow-database-owner-difference", metavar="FIRST:SECOND",
        help="Accept exactly this database-owner pair (e.g. leia_app:postgres); "
             "table owners are still compared.")
    args = parser.parse_args(argv)
    if args.command == "verify-migration":
        return verify_migration(args.before, args.after, args.applied)
    if args.command == "verify-adoption":
        return verify_adoption(args.before, args.after, args.added)
    if args.command == "compare-status":
        owners = None
        if args.allow_database_owner_difference:
            parts = args.allow_database_owner_difference.split(":")
            if len(parts) != 2 or not all(parts):
                parser.error("--allow-database-owner-difference needs FIRST:SECOND")
            owners = (parts[0], parts[1])
        return compare_status(args.first, args.second, owners)
    if args.owner:
        os.environ["DB_USER"] = args.owner
        os.environ["DB_PASSWORD"] = getpass("Database owner password: ")
    with get_connection() as connection:
        if args.command == "adopt":
            return adopt(connection, args.through, args.apply, args.expect_record)
        if args.command == "status":
            return status(connection, args.json, args.output)
        return status(connection)


if __name__ == "__main__":
    sys.exit(main())
