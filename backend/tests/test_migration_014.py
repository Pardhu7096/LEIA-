"""Migration 014 against existing data, then rollback (recovery), on an empty disposable database.

Applies 000-013 exactly as files, creates representative existing records (listing with every
pre-014 field, private details, media, chat, sessions, Studio job), applies 014 and checks that no
existing value changed and every new fact starts as unknown/empty. Then runs the documented rollback
and checks the pre-014 schema and data are intact.
"""
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
ROLLBACK_014 = """
ALTER TABLE public.properties
    DROP CONSTRAINT properties_floor_within_building,
    DROP CONSTRAINT properties_possession_needs_status,
    DROP CONSTRAINT properties_rera_source_needs_number,
    DROP CONSTRAINT properties_rera_verified_by_needs_check,
    DROP COLUMN builder_id, DROP COLUMN project_name, DROP COLUMN rera_number, DROP COLUMN rera_source,
    DROP COLUMN rera_checked_on, DROP COLUMN rera_verified_by, DROP COLUMN construction_status,
    DROP COLUMN possession_date, DROP COLUMN floor_number, DROP COLUMN total_floors, DROP COLUMN lift,
    DROP COLUMN gated_community, DROP COLUMN security, DROP COLUMN amenities, DROP COLUMN project_notes,
    DROP COLUMN project_source, DROP COLUMN project_checked_on;
DROP TABLE public.builders;
"""
NEW_COLUMNS = {"builder_id", "project_name", "rera_number", "rera_source", "rera_checked_on", "rera_verified_by",
               "construction_status", "possession_date", "floor_number", "total_floors", "lift",
               "gated_community", "security", "amenities", "project_notes", "project_source", "project_checked_on"}


@pytest.fixture
def connection():
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url or (urlparse(url).path or "/").lstrip("/") != "leia_migration_test":
        pytest.skip("Runs only against the empty disposable database leia_migration_test.")
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('public.users') AS t").fetchone()["t"] is not None:
            pytest.fail("leia_migration_test must be empty.")
        conn.execute("""CREATE TABLE public.users (id UUID PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
                        role TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""")
        for path in sorted(MIGRATIONS.glob("0*.sql")):
            if path.name < "014":
                conn.execute(path.read_text(encoding="utf-8"))
        yield conn


def snapshot(conn, table, key="id"):
    return {str(row[key]): row for row in conn.execute(f"SELECT * FROM public.{table} ORDER BY {key}")}


def test_existing_records_survive_014_and_its_rollback(connection):
    conn = connection
    agent, admin = uuid4(), uuid4()
    conn.execute("INSERT INTO users (id, name, email, role) VALUES (%s,'Agent','a@example.com','agent'),"
                 " (%s,'Admin','b@example.com','admin')", (agent, admin))
    listings = []
    for status, facing in (("approved", "north"), ("draft", "southwest"), ("submitted", None)):
        pid = uuid4()
        listings.append(pid)
        conn.execute(
            """INSERT INTO properties (id, created_by, title, property_type, price, bedrooms, bathrooms, area_sqft,
                   locality, city, latitude, longitude, status, availability, facing, car_parking_spaces,
                   parking_type, power_backup, water_municipal, maintenance_monthly, location_source,
                   reviewed_at)
               VALUES (%s,%s,%s,'flat',6400000,3,2,1250.50,'Madhurawada','Visakhapatnam',17.8,83.35,%s,
                   'available',%s,1,'covered','full_home','yes',2500,'manual',CURRENT_TIMESTAMP)""",
            (pid, agent, f"Existing {status}", status, facing))
        conn.execute("INSERT INTO property_private_details (property_id, owner_name, owner_phone, listing_consent)"
                     " VALUES (%s,'Owner','+91 90000 00001',TRUE)", (pid,))
    conn.execute("INSERT INTO property_media (id, property_id, kind, content) VALUES (%s,%s,'cover',%s)",
                 (uuid4(), listings[0], b"\xff\xd8\xff\xe0jpeg"))
    before = {table: snapshot(conn, table, key) for table, key in
              (("properties", "id"), ("property_private_details", "property_id"), ("property_media", "id"),
               ("users", "id"))}

    conn.execute((MIGRATIONS / "014_builder_project_facts.sql").read_text(encoding="utf-8"))

    after = snapshot(conn, "properties")
    for pid, old in before["properties"].items():
        new = after[pid]
        assert {k: new[k] for k in old} == old, "existing values must not change"
        assert new["lift"] == new["gated_community"] == new["security"] == "unknown"
        assert new["construction_status"] == "unknown" and new["rera_source"] == "unknown"
        assert new["amenities"] == [] and new["builder_id"] is None and new["rera_number"] is None
    for table, key in (("property_private_details", "property_id"), ("property_media", "id"), ("users", "id")):
        assert snapshot(conn, table, key) == before[table], table

    # Constraints guard the new facts.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE properties SET possession_date = '2027-01-01' WHERE id = %s", (listings[0],))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE properties SET floor_number = 9, total_floors = 5 WHERE id = %s", (listings[0],))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE properties SET rera_source = 'document_checked' WHERE id = %s", (listings[0],))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE properties SET amenities = '{helipad}' WHERE id = %s", (listings[0],))
    conn.execute("INSERT INTO builders (id, name, created_by) VALUES (%s, 'Raju Constructions', %s)", (uuid4(), agent))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO builders (id, name, created_by) VALUES (%s, ' raju  CONSTRUCTIONS.', %s)",
                     (uuid4(), agent))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO builders (id, name, created_by) VALUES (%s, '!!!', %s)", (uuid4(), agent))

    # Recovery: the documented rollback restores the pre-014 schema with every record intact.
    conn.execute(ROLLBACK_014)
    columns = {r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'properties'")}
    assert not columns & NEW_COLUMNS
    assert conn.execute("SELECT to_regclass('public.builders') AS t").fetchone()["t"] is None
    assert snapshot(conn, "properties") == before["properties"]
    for table, key in (("property_private_details", "property_id"), ("property_media", "id"), ("users", "id")):
        assert snapshot(conn, table, key) == before[table], table
