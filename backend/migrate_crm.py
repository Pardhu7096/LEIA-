"""Run ordered, tracked CRM migrations with the database owner's credentials.

    python migrate_crm.py --owner postgres --dry-run                  # read-only plan
    python migrate_crm.py --owner postgres --expect 007_room_panoramas.sql

--dry-run runs in a read-only session and prints every pending migration with its
SHA-256 and full SQL; nothing is written. --expect NAME (repeatable) refuses, before
running anything, unless exactly those migrations are pending. A real run applies
all pending migrations in one transaction: on any error nothing is applied.
"""

import argparse
import hashlib
import os
import sys
from getpass import getpass
from pathlib import Path

from database import get_connection

MIGRATIONS = Path(__file__).parent / "migrations"
LOCK_ID = 72180411


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def migrate(dry_run: bool = False, expect: list[str] | None = None) -> int:
    with get_connection() as connection:
        if dry_run:
            connection.read_only = True  # A dry run cannot write, even by mistake.
        with connection.cursor() as cursor:
            # Serialize concurrent migration runners for this database.
            cursor.execute("SELECT pg_advisory_xact_lock(%s);", (LOCK_ID,))
            cursor.execute(
                "SELECT to_regclass('public.crm_schema_migrations') IS NOT NULL AS tracked;")
            tracked = cursor.fetchone()["tracked"]
            applied = set()
            if tracked:
                cursor.execute("SELECT name FROM public.crm_schema_migrations;")
                applied = {row["name"] for row in cursor.fetchall()}
            pending = [p for p in sorted(MIGRATIONS.glob("*.sql")) if p.name not in applied]
            names = [p.name for p in pending]
            if expect is not None and sorted(expect) != names:
                print(f"Expected pending {', '.join(sorted(expect)) or '(none)'}, found "
                      f"{', '.join(names) or '(none)'}; refusing. Nothing changed.")
                return 2
            if dry_run:
                print("Dry run (read-only session). In one transaction the runner would:")
                if not tracked:
                    print("  create public.crm_schema_migrations")
                print("  grant SELECT on public.crm_schema_migrations to leia_app (if missing)")
                for path in pending:
                    print(f"  apply {path.name}  SHA-256 {file_sha256(path)}")
                    for line in path.read_text(encoding="utf-8").strip().splitlines():
                        print(f"    | {line}")
                    print(f"  record {path.name} in public.crm_schema_migrations")
                if not pending:
                    print("  nothing: the database is up to date")
                print("Dry run: nothing changed.")
                connection.rollback()
                return 0
            cursor.execute("""CREATE TABLE IF NOT EXISTS public.crm_schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );""")
            # Read-only access lets the API's readiness probe detect pending migrations.
            cursor.execute("""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
                    GRANT SELECT ON public.crm_schema_migrations TO leia_app;
                END IF;
            END $$;""")
            for path in pending:
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO public.crm_schema_migrations (name) VALUES (%s);",
                    (path.name,),
                )
                print(f"Applied {path.name}")
    print("CRM database is up to date.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--owner",
        help="Database owner, e.g. postgres; prompts privately for its password.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Read-only: print what would run; change nothing.")
    parser.add_argument("--expect", action="append", metavar="NAME",
                        help="Refuse unless exactly these migrations are pending.")
    args = parser.parse_args()
    if args.owner:
        os.environ["DB_USER"] = args.owner
        os.environ["DB_PASSWORD"] = getpass("Database owner password: ")
    sys.exit(migrate(args.dry_run, args.expect))
