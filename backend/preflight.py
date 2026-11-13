"""Deployment preflight: validate configuration and database schema.

Run before a release is switched live:

    python preflight.py --deployed

Exits non-zero and lists every problem found. Only setting NAMES are printed,
never their values, so the output is safe for CI and server logs.
"""

import argparse
import os
import re
import sys
from pathlib import Path

REQUIRED_TABLES = (
    "users",
    "auth_sessions",
    "conversations",
    "chat_messages",
    "properties",
    "property_private_details",
    "property_media",
    "request_limits",
)
REQUIRED_SETTINGS = ("GROQ_API_KEY", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
SECRET_SETTINGS = ("JWT_SECRET", "GROQ_API_KEY", "DB_PASSWORD")
PLACEHOLDER_PREFIX = "CHANGE_ME"
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
DEPLOYED_ORIGIN = re.compile(r"https://([A-Za-z0-9.-]+)(?::\d+)?")
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def parse_origins(raw: str | None) -> set[str]:
    """Split a comma-separated origin list, ignoring blanks and trailing slashes."""
    return {item.strip().rstrip("/") for item in (raw or "").split(",") if item.strip()}


def environment_problems(env, *, deployed: bool = False) -> list[str]:
    """Return configuration problems. `deployed` adds the HTTPS-only server rules."""
    problems = []
    if len(env.get("JWT_SECRET", "")) < 32:
        problems.append("JWT_SECRET must be set to at least 32 characters.")
    for name in REQUIRED_SETTINGS:
        if not env.get(name, "").strip():
            problems.append(f"{name} is not set.")
    port = env.get("DB_PORT", "")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        problems.append("DB_PORT must be a port number.")
    if not deployed:
        return problems

    for name in SECRET_SETTINGS:
        if env.get(name, "").startswith(PLACEHOLDER_PREFIX):
            problems.append(f"{name} still holds the example placeholder.")
    if env.get("COOKIE_SECURE", "").strip().lower() != "true":
        problems.append("COOKIE_SECURE must be true on a deployed server (HTTPS only).")
    origins = parse_origins(env.get("LEIA_ALLOWED_ORIGINS"))
    if not origins:
        problems.append(
            "LEIA_ALLOWED_ORIGINS must list the HTTPS web origins allowed to call the API."
        )
    for origin in sorted(origins):
        match = DEPLOYED_ORIGIN.fullmatch(origin)
        if not match or match[1].lower() in LOCAL_HOSTS:
            problems.append(
                "LEIA_ALLOWED_ORIGINS may only contain origins such as "
                "https://app.example.com (HTTPS, a real host name, no path)."
            )
            break
    return problems


def environment_warnings(env) -> list[str]:
    missing = [
        name
        for name in ("GOOGLE_PLACES_API_KEY", "GOOGLE_ROUTES_API_KEY")
        if not env.get(name, "").strip()
    ]
    if missing:
        return [f"{', '.join(missing)} not set: nearby places/walking estimates are disabled."]
    return []


def database_problems(connect=None) -> list[str]:
    """Check the database is reachable and has every migration this release ships."""
    import psycopg
    from psycopg.errors import InsufficientPrivilege

    if connect is None:
        from database import get_connection as connect
    try:
        with connect() as connection:
            missing = [
                name
                for name in REQUIRED_TABLES
                if connection.execute(
                    "SELECT to_regclass(%s) AS found;", (f"public.{name}",)
                ).fetchone()["found"]
                is None
            ]
            if missing:
                return [f"Missing tables ({', '.join(missing)}): run migrate_crm.py."]
            history = connection.execute(
                "SELECT to_regclass('public.crm_schema_migrations') AS found;"
            ).fetchone()["found"]
            if history is None:
                return ["Migration history is missing: run migrate_crm.py."]
            applied = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM public.crm_schema_migrations;"
                ).fetchall()
            }
    except InsufficientPrivilege:
        return [
            "The API database role cannot read public.crm_schema_migrations: "
            "rerun migrate_crm.py as the owner or grant SELECT on it."
        ]
    except (psycopg.Error, OSError, KeyError, ValueError) as error:
        # The exception text can include host and user names; report only its type.
        return [f"Database is unreachable or misconfigured ({type(error).__name__})."]
    pending = sorted(path.name for path in MIGRATIONS.glob("*.sql") if path.name not in applied)
    if pending:
        return [f"Pending migrations ({', '.join(pending)}): run migrate_crm.py."]
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--deployed", action="store_true",
        help="Apply the rules for an HTTPS server (staging or production).",
    )
    parser.add_argument(
        "--skip-database", action="store_true",
        help="Validate settings only, without connecting to the database.",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    problems = environment_problems(os.environ, deployed=args.deployed)
    if not problems and not args.skip_database:
        problems = database_problems()
    for warning in environment_warnings(os.environ):
        print(f"WARNING: {warning}")
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("Preflight passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
