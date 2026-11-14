"""Run ordered, tracked CRM migrations with the database owner's credentials."""

import argparse
import os
from getpass import getpass
from pathlib import Path

from database import get_connection


def migrate():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Serialize concurrent migration runners for this database.
            cursor.execute("SELECT pg_advisory_xact_lock(72180411);")
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
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                cursor.execute(
                    "SELECT 1 FROM public.crm_schema_migrations WHERE name = %s;",
                    (path.name,),
                )
                if cursor.fetchone():
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO public.crm_schema_migrations (name) VALUES (%s);",
                    (path.name,),
                )
                print(f"Applied {path.name}")
    print("CRM database is up to date.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner",
        help="Database owner, e.g. postgres; prompts privately for its password.",
    )
    args = parser.parse_args()
    if args.owner:
        os.environ["DB_USER"] = args.owner
        os.environ["DB_PASSWORD"] = getpass("Database owner password: ")
    migrate()
