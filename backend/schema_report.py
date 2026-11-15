"""Describe or compare the LEIA database schema, read-only.

Prints every table in the public schema: columns (type, nullability, default,
identity), constraints and indexes, normalised so two databases can be compared
regardless of column order or PostgreSQL version (16 and 18 both supported).

    python schema_report.py                      # describe the database in backend/.env
    python schema_report.py --compare tests/expected_schema.json

--compare exits 1 and lists every difference when the database does not match.
The session is read-only: this tool never changes a database.
"""

import argparse
import json
import sys
from pathlib import Path

from database import get_connection

# NOT NULL is reported per column; PostgreSQL 18 also lists it as a constraint
# ('n'), which would otherwise make 16 and 18 look different.
CONSTRAINT_TYPES = ("c", "f", "p", "u", "x")


def describe(connection) -> dict:
    """Return {table: {columns, constraints, indexes}} for the public schema."""
    rows = connection.execute(
        """
        SELECT c.relname AS table_name, a.attname AS column_name,
               format_type(a.atttypid, a.atttypmod) AS type,
               NOT a.attnotnull AS nullable,
               pg_get_expr(d.adbin, d.adrelid) AS "default",
               NULLIF(a.attidentity, '') AS identity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE c.relkind IN ('r', 'p')
        """
    ).fetchall()
    schema: dict = {}
    for row in rows:
        table = schema.setdefault(
            row["table_name"], {"columns": {}, "constraints": {}, "indexes": {}}
        )
        table["columns"][row["column_name"]] = {
            "type": row["type"],
            "nullable": row["nullable"],
            "default": row["default"],
            "identity": {"a": "always", "d": "by default"}.get(row["identity"]),
        }
    for row in connection.execute(
        """
        SELECT c.relname AS table_name, k.conname AS name,
               pg_get_constraintdef(k.oid) AS definition
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        WHERE k.contype = ANY(%s)
        """,
        (list(CONSTRAINT_TYPES),),
    ).fetchall():
        if row["table_name"] in schema:
            schema[row["table_name"]]["constraints"][row["name"]] = row["definition"]
    for row in connection.execute(
        """
        SELECT t.relname AS table_name, i.relname AS name,
               pg_get_indexdef(x.indexrelid) AS definition
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_class t ON t.oid = x.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = 'public'
        """
    ).fetchall():
        if row["table_name"] in schema:
            schema[row["table_name"]]["indexes"][row["name"]] = row["definition"]
    return {name: schema[name] for name in sorted(schema)}


def differences(expected: dict, actual: dict) -> list[str]:
    """Human-readable differences, from the point of view of `actual`."""
    found = []
    for table in sorted(set(expected) | set(actual)):
        if table not in actual:
            found.append(f"{table}: table missing")
            continue
        if table not in expected:
            found.append(f"{table}: unexpected table")
            continue
        for part in ("columns", "constraints", "indexes"):
            want, have = expected[table][part], actual[table][part]
            for name in sorted(set(want) | set(have)):
                if name not in have:
                    found.append(f"{table}.{part}.{name}: missing (expected {want[name]})")
                elif name not in want:
                    found.append(f"{table}.{part}.{name}: unexpected ({have[name]})")
                elif want[name] != have[name]:
                    found.append(
                        f"{table}.{part}.{name}: expected {want[name]}, found {have[name]}"
                    )
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compare", type=Path, help="Expected schema JSON file.")
    args = parser.parse_args(argv)
    with get_connection() as connection:
        connection.read_only = True
        # Fixed search path so references print the same way for every login.
        connection.execute("SELECT set_config('search_path', 'public', true)")
        actual = describe(connection)
    if not args.compare:
        print(json.dumps(actual, indent=2, sort_keys=True))
        return 0
    found = differences(json.loads(args.compare.read_text(encoding="utf-8")), actual)
    for line in found:
        print(line)
    print("Schema matches." if not found else f"{len(found)} difference(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
