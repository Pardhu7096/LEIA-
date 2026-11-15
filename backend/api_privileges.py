"""Least privilege for the API's database role: report and plan, read-only.

    python api_privileges.py --owner postgres                       # inventory of leia_app
    python api_privileges.py --owner postgres --role leia_app --plan-file m12.sql

The inventory compares what the role can do with what the API needs (REQUIRED,
derived from every SQL statement in the API and identical to what the migrations
grant). --plan-file writes the SQL that would make them equal: ownership of the
database and tables moves to the owner role, every privilege the API does not need
is removed, and exactly REQUIRED is granted. The plan is written for review only;
this tool never changes a database (read-only session).
"""

import argparse
import os
import sys
from getpass import getpass
from pathlib import Path

from database import get_connection

# Table privileges the API needs. Row locks (SELECT ... FOR UPDATE/SHARE) and
# INSERT ... ON CONFLICT DO UPDATE need UPDATE; DELETE/UPDATE with WHERE and
# RETURNING need SELECT. No sequence privilege is needed: chat_messages.id is an
# identity column. Foreign-key checks and ON DELETE CASCADE run as the table owner.
REQUIRED = {
    "users": {"SELECT", "INSERT"},
    "auth_sessions": {"SELECT", "INSERT", "UPDATE"},
    "conversations": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "chat_messages": {"SELECT", "INSERT", "DELETE"},
    "properties": {"SELECT", "INSERT", "UPDATE"},
    "property_private_details": {"SELECT", "INSERT", "UPDATE"},
    "property_media": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "request_limits": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "crm_schema_migrations": {"SELECT"},
}
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
ROLE_FLAGS = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")


def inventory(connection, role: str) -> dict:
    """Everything the role owns or may do in the current database."""
    connection.read_only = True
    found = connection.execute(
        f"SELECT {', '.join(ROLE_FLAGS)} FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    if found is None:
        raise SystemExit(f"Role {role!r} does not exist.")
    report = {"role": role, "flags": {k: v for k, v in found.items() if v}}
    report["member_of"] = [r["rolname"] for r in connection.execute(
        """SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
           JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = %s ORDER BY 1""", (role,))]
    database = connection.execute(
        """SELECT current_database() AS name, pg_get_userbyid(datdba) AS owner,
                  has_database_privilege(%(r)s, current_database(), 'CREATE') AS can_create,
                  has_database_privilege(%(r)s, current_database(), 'TEMP') AS can_temp,
                  has_database_privilege(%(r)s, current_database(), 'CONNECT') AS can_connect
           FROM pg_database WHERE datname = current_database()""", {"r": role}).fetchone()
    report["database"] = dict(database)
    report["schema_public"] = dict(connection.execute(
        """SELECT pg_get_userbyid(nspowner) AS owner,
                  has_schema_privilege(%(r)s, 'public', 'CREATE') AS can_create,
                  has_schema_privilege(%(r)s, 'public', 'USAGE') AS can_use
           FROM pg_namespace WHERE nspname = 'public'""", {"r": role}).fetchone())
    checks = ", ".join(
        f"has_table_privilege(%(r)s, c.oid, '{p}') AS \"{p}\"" for p in TABLE_PRIVILEGES)
    report["tables"] = {}
    for row in connection.execute(
            f"""SELECT c.relname, pg_get_userbyid(c.relowner) AS owner, {checks}
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1""",
            {"r": role}):
        report["tables"][row["relname"]] = {
            "owner": row["owner"], "privileges": sorted(p for p in TABLE_PRIVILEGES if row[p])}
    report["sequences"] = {
        row["relname"]: {"owner": row["owner"], "table": row["table_name"], "privileges": [
            p for p in ("USAGE", "SELECT", "UPDATE") if row[p]]}
        for row in connection.execute(
            """SELECT c.relname, pg_get_userbyid(c.relowner) AS owner,
                      (SELECT t.relname FROM pg_depend d JOIN pg_class t ON t.oid = d.refobjid
                       WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                         AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
                       LIMIT 1) AS table_name,
                      has_sequence_privilege(%(r)s, c.oid, 'USAGE') AS "USAGE",
                      has_sequence_privilege(%(r)s, c.oid, 'SELECT') AS "SELECT",
                      has_sequence_privilege(%(r)s, c.oid, 'UPDATE') AS "UPDATE"
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind = 'S' ORDER BY 1""", {"r": role})}
    # Anything else the role owns in this database (functions, types, views, schemas).
    report["other_owned"] = [r["what"] for r in connection.execute(
        """SELECT 'schema ' || nspname AS what FROM pg_namespace
             WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = %(r)s)
               AND nspname <> 'public'
           UNION ALL
           SELECT 'function ' || p.oid::regprocedure::text FROM pg_proc p
             WHERE proowner = (SELECT oid FROM pg_roles WHERE rolname = %(r)s)
           UNION ALL
           SELECT 'relation ' || c.oid::regclass::text || ' (' || c.relkind::text || ')' FROM pg_class c
             WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = %(r)s)
               AND relkind NOT IN ('r', 'p', 'S', 'i', 'I', 't')
           UNION ALL
           SELECT 'type ' || t.oid::regtype::text FROM pg_type t
             WHERE typowner = (SELECT oid FROM pg_roles WHERE rolname = %(r)s)
               AND typtype IN ('d', 'e', 'r', 'm') ORDER BY 1""", {"r": role})]
    report["default_privileges"] = [r["d"] for r in connection.execute(
        """SELECT coalesce(n.nspname, '(all schemas)') || ' ' || d.defaclobjtype::text || ' '
                  || d.defaclacl::text AS d
           FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
           WHERE d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = %(r)s)
              OR d.defaclacl::text LIKE '%%' || %(r)s || '=%%'""", {"r": role})]
    return report


def problems(report: dict) -> list[str]:
    """Every way the role differs from least privilege. Empty means compliant."""
    role, found = report["role"], []
    for flag in report["flags"]:
        found.append(f"role attribute {flag[3:].upper()}")
    if report["member_of"]:
        found.append(f"member of {', '.join(report['member_of'])} (inherits their rights)")
    database = report["database"]
    if database["owner"] == role:
        found.append(f"owns database {database['name']}")
    if database["can_create"]:
        found.append(f"may CREATE schemas in database {database['name']}")
    if not database["can_connect"]:
        found.append(f"cannot CONNECT to database {database['name']}")
    schema = report["schema_public"]
    if schema["owner"] == role:
        found.append("owns schema public")
    if schema["can_create"]:
        found.append("may CREATE objects in schema public")
    if not schema["can_use"]:
        found.append("has no USAGE on schema public")
    for table, info in report["tables"].items():
        if info["owner"] == role:
            found.append(f"owns table {table}")
        have, need = set(info["privileges"]), REQUIRED.get(table, set())
        if have - need:
            found.append(f"{table}: not needed {', '.join(sorted(have - need))}")
        if need - have:
            found.append(f"{table}: missing {', '.join(sorted(need - have))}")
    for table in sorted(set(REQUIRED) - set(report["tables"])):
        found.append(f"{table}: table does not exist")
    for sequence, info in report["sequences"].items():
        if info["owner"] == role:
            found.append(f"owns sequence {sequence}")
        if info["privileges"]:
            found.append(f"sequence {sequence}: not needed {', '.join(info['privileges'])}")
    found += [f"owns {what}" for what in report["other_owned"]]
    found += [f"default privileges: {d}" for d in report["default_privileges"]]
    return found


def plan(report: dict, owner: str) -> str:
    """SQL that makes the role exactly least-privileged. For review; never executed here."""
    role, database = report["role"], report["database"]["name"]
    if report["other_owned"] or report["default_privileges"] or report["member_of"]:
        raise SystemExit("The role owns other objects, has default privileges or role "
                         "memberships; these need a manual review before any plan.")
    q = lambda name: '"' + name.replace('"', '""') + '"'  # noqa: E731
    lines = [
        f"-- M12: make {role} least-privileged in database {database}.",
        f"-- Generated from a read-only inventory; run as {owner} after review and approval,",
        "-- with the backend stopped. One transaction: on any error nothing changes.",
        "BEGIN;",
        "SET LOCAL lock_timeout = '5s';",
        "-- Changing a table's owner also moves its indexes and identity sequences.",
    ]
    if report["database"]["owner"] == role:
        lines.append(f"ALTER DATABASE {q(database)} OWNER TO {q(owner)};")
    for table, info in report["tables"].items():
        if info["owner"] == role:
            lines.append(f"ALTER TABLE public.{q(table)} OWNER TO {q(owner)};")
    for sequence, info in report["sequences"].items():
        # A sequence that belongs to a table column (identity/serial) moves with the
        # table; PostgreSQL refuses to change its owner on its own.
        if info["owner"] == role and not info["table"]:
            lines.append(f"ALTER SEQUENCE public.{q(sequence)} OWNER TO {q(owner)};")
    lines += [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {q(role)};",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {q(role)};",
        f"REVOKE CREATE ON SCHEMA public FROM {q(role)};",
        f"REVOKE CREATE ON DATABASE {q(database)} FROM {q(role)};",
        f"GRANT CONNECT ON DATABASE {q(database)} TO {q(role)};",
        f"GRANT USAGE ON SCHEMA public TO {q(role)};",
    ]
    for table in sorted(REQUIRED):
        if table in report["tables"]:
            lines.append(f"GRANT {', '.join(sorted(REQUIRED[table]))} ON public.{q(table)} "
                         f"TO {q(role)};")
    if report["flags"]:
        lines.append(f"ALTER ROLE {q(role)} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                     "NOBYPASSRLS;  -- cluster-wide")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def print_inventory(report: dict) -> None:
    db, schema = report["database"], report["schema_public"]
    print(f"role: {report['role']}; attributes: {', '.join(report['flags']) or 'none'}; "
          f"member of: {', '.join(report['member_of']) or 'none'}")
    print(f"database {db['name']}: owner {db['owner']}; CONNECT {db['can_connect']}, "
          f"CREATE {db['can_create']}, TEMP {db['can_temp']}")
    print(f"schema public: owner {schema['owner']}; USAGE {schema['can_use']}, "
          f"CREATE {schema['can_create']}")
    print("tables (owner; role's effective privileges | required):")
    for table, info in report["tables"].items():
        need = ", ".join(sorted(REQUIRED.get(table, set()))) or "-"
        print(f"  {table}: {info['owner']}; {', '.join(info['privileges']) or '-'} | {need}")
    for sequence, info in report["sequences"].items():
        part = f" (column of {info['table']})" if info["table"] else ""
        print(f"  sequence {sequence}{part}: {info['owner']}; "
              f"{', '.join(info['privileges']) or '-'}")
    for what in report["other_owned"]:
        print(f"  also owns {what}")
    for d in report["default_privileges"]:
        print(f"  default privileges: {d}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner", help="Connect as this owner role; prompts for its password.")
    parser.add_argument("--role", default="leia_app", help="API role to inspect.")
    parser.add_argument("--plan-owner", default="postgres", help="Owner role for the plan.")
    parser.add_argument("--plan-file", type=Path, help="Write the remediation SQL here.")
    args = parser.parse_args(argv)
    if args.owner:
        os.environ["DB_USER"] = args.owner
        os.environ["DB_PASSWORD"] = getpass("Database owner password: ")
    with get_connection() as connection:
        report = inventory(connection, args.role)
    print_inventory(report)
    found = problems(report)
    print("least-privilege problems:" if found else "least privilege: compliant")
    for line in found:
        print(f"  - {line}")
    if args.plan_file:
        args.plan_file.write_text(plan(report, args.plan_owner), encoding="utf-8")
        print(f"Plan written to {args.plan_file} (not executed).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
