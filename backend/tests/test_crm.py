"""CRM regression tests. Integration tests require an isolated TEST_DATABASE_URL."""

import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault(
    "JWT_SECRET", "isolated-test-secret-never-use-in-production-12345"
)
import crm
from auth import create_access_token, require_staff
from migrate_crm import migrate
from property_models import PropertyDraft


def test_unknown_is_not_zero_and_private_fields_are_allowed():
    draft = PropertyDraft(title="Home", property_type="flat", car_parking_spaces=0)
    assert draft.car_parking_spaces == 0
    assert draft.bike_parking_spaces is None
    assert draft.water_municipal == "unknown"
    assert draft.electricity_connection == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"car_parking_spaces": -1},
        {"car_parking_spaces": 1.5},
        {"water_storage_litres": -1},
        {"water_monthly_charge": -1},
        {"latitude": 12},
        {"latitude": float("nan"), "longitude": 15},
        {"electricity_connection": "maybe"},
        {"parking_source": "guessed"},
        {"parking_checked_on": "2026-02-30"},
        {"status": "approved"},
        {"created_by": str(uuid4())},
        {"title": "   "},
    ],
)
def test_invalid_or_server_managed_fields_rejected(changes):
    with pytest.raises(ValidationError):
        PropertyDraft.model_validate(
            {"title": "Home", "property_type": "flat", **changes}
        )


@pytest.fixture(scope="module")
def database():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated disposable database.")

    # Safety: never use the application's production connection or public tables.
    def connect():
        return psycopg.connect(url, row_factory=dict_row)

    original = crm.get_connection
    import migrate_crm

    original_migrate = migrate_crm.get_connection
    try:
        # Tests use a dedicated database; explicit guard prevents accidental leia use.
        with connect() as connection:
            name = connection.execute("SELECT current_database() AS name").fetchone()[
                "name"
            ]
            if name not in {"postgres", "leia_test", "crm_test"}:
                pytest.fail("Use a disposable database named leia_test or crm_test.")
            # A guard table prevents overwriting any database that already has users.
            exists = connection.execute(
                "SELECT to_regclass('public.users') AS table_name"
            ).fetchone()["table_name"]
            if exists:
                pytest.fail(
                    "TEST_DATABASE_URL must point to an empty disposable database."
                )
            connection.execute("""CREATE TABLE public.users (
                id UUID PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
                role TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );""")
        # Reproduce the user's existing baseline and a saved draft before upgrading.
        legacy_owner, legacy_id = uuid4(), uuid4()
        with connect() as connection:
            baseline = (
                Path(__file__).resolve().parents[1]
                / "migrations"
                / "001_property_drafts.sql"
            )
            connection.execute(baseline.read_text())
            connection.execute(
                "INSERT INTO users (id,name,email,role) VALUES (%s,'Legacy','legacy@example.com','agent')",
                (legacy_owner,),
            )
            connection.execute(
                "INSERT INTO properties (id,created_by,title,property_type,price) VALUES (%s,%s,'Existing draft','flat',7500000)",
                (legacy_id, legacy_owner),
            )
        crm.get_connection = connect
        migrate_crm.get_connection = connect
        migrate()
        with connect() as connection:
            legacy = connection.execute(
                "SELECT title,price,parking_type,car_parking_spaces FROM properties WHERE id=%s",
                (legacy_id,),
            ).fetchone()
            assert legacy == {
                "title": "Existing draft",
                "price": 7500000,
                "parking_type": "unknown",
                "car_parking_spaces": None,
            }
        # Tracked rerun must be harmless.
        migrate()
        yield connect
    finally:
        crm.get_connection = original
        migrate_crm.get_connection = original_migrate
        # Leave cleanup to the disposable database owner; never drop application tables.


@pytest.fixture
def client(database):
    app = FastAPI()
    app.include_router(crm.router)
    with TestClient(app) as client:
        yield app, client, database


def staff(connect, role):
    user = {
        "id": uuid4(),
        "name": "Test staff",
        "email": f"{uuid4()}@example.com",
        "role": role,
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, name, email, role) VALUES (%s, %s, %s, %s)",
            tuple(user[key] for key in ("id", "name", "email", "role")),
        )
    return user


def test_complete_edit_and_permissions(client):
    app, http, connect = client
    owner, other, admin, customer = [
        staff(connect, role) for role in ("agent", "agent", "admin", "customer")
    ]

    def as_user(user):
        app.dependency_overrides[require_staff] = lambda: user

    as_user(owner)
    created = http.post(
        "/staff/properties",
        json={
            "title": "Madhura Apartments",
            "property_type": "flat",
            "price": 7500000,
            "area_sqft": "1200.25",
            "owner_name": "Private owner",
            "owner_phone": "private-phone",
            "agent_notes": "Do not publish",
            "listing_consent": True,
        },
    )
    assert created.status_code == 201, created.text
    property_id = created.json()["id"]
    endpoint = f"/staff/properties/{property_id}"
    detail = http.get(endpoint)
    assert detail.status_code == 200, detail.text
    assert detail.headers["cache-control"] == "no-store"
    record = detail.json()
    assert record["parking_type"] == "unknown"
    assert record["car_parking_spaces"] is None
    assert record["area_sqft"] == "1200.25"
    with connect() as connection:
        before = connection.execute(
            "SELECT consent_recorded_at FROM property_private_details WHERE property_id=%s",
            (property_id,),
        ).fetchone()
        connection.execute(
            "UPDATE property_private_details SET owner_email=%s, review_notes=%s WHERE property_id=%s",
            ("private@example.com", "Admin internal", property_id),
        )
    editable = {key: record[key] for key in PropertyDraft.model_fields}
    updated = http.put(
        endpoint,
        json={
            **editable,
            "version": record["version"],
            "car_parking_spaces": 1,
            "bike_parking_spaces": 0,
            "parking_type": "covered",
            "parking_source": "observed",
            "parking_checked_on": "2026-11-04",
            "electricity_connection": "yes",
            "electricity_meter": "separate",
            "power_backup": "partial_home",
            "water_municipal": "yes",
            "water_borewell": "no",
            "water_tanker": "unknown",
            "water_supply_frequency": "daily",
            "water_storage_litres": 2000,
            "water_monthly_charge": 0,
            "maintenance_monthly": 1500,
            "water_notes": "Owner reports daily supply",
            "water_source": "owner_reported",
        },
    )
    assert updated.status_code == 200, updated.text
    saved = http.get(endpoint).json()
    assert saved["version"] == record["version"] + 1
    assert saved["updated_at"] != record["updated_at"]
    assert saved["car_parking_spaces"] == 1 and saved["bike_parking_spaces"] == 0
    assert (
        saved["owner_phone"] == "private-phone"
        and saved["agent_notes"] == "Do not publish"
    )
    assert saved["area_sqft"] == "1200.25"
    assert saved["water_tanker"] == "unknown" and saved["water_monthly_charge"] == 0
    assert (
        saved["water_source"] == "owner_reported"
        and saved["parking_checked_on"] == "2026-11-04"
    )
    assert (
        http.put(endpoint, json={**editable, "version": record["version"]}).status_code
        == 409
    )
    with connect() as connection:
        private = connection.execute(
            "SELECT * FROM property_private_details WHERE property_id=%s",
            (property_id,),
        ).fetchone()
        assert (
            private["owner_email"] == "private@example.com"
            and private["review_notes"] == "Admin internal"
        )
        assert private["consent_recorded_at"] == before["consent_recorded_at"]
    listing = http.get("/staff/properties").json()
    assert property_id in [item["id"] for item in listing]
    assert all(
        "owner_phone" not in item and "agent_notes" not in item for item in listing
    )
    as_user(other)
    assert http.get(endpoint).status_code == 404
    assert (
        http.put(endpoint, json={**editable, "version": saved["version"]}).status_code
        == 404
    )
    assert property_id not in [
        item["id"] for item in http.get("/staff/properties").json()
    ]
    as_user(admin)
    assert http.get(endpoint).status_code == 200
    assert property_id in [item["id"] for item in http.get("/staff/properties").json()]
    assert (
        http.put(endpoint, json={**editable, "version": saved["version"]}).status_code
        == 200
    )
    with connect() as connection:
        connection.execute(
            "UPDATE properties SET status='submitted' WHERE id=%s", (property_id,)
        )
    assert (
        http.put(
            endpoint, json={**editable, "version": saved["version"] + 1}
        ).status_code
        == 409
    )
    app.dependency_overrides.clear()
    assert http.get(endpoint).status_code == 401
    assert (
        http.put(endpoint, json={**editable, "version": saved["version"]}).status_code
        == 401
    )
    # Use real JWT auth and a real current database role for the customer denial.
    import auth

    original = auth.get_connection
    auth.get_connection = connect
    try:
        token = create_access_token(customer["id"])["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert http.get(endpoint, headers=headers).status_code == 403
        assert (
            http.put(
                endpoint,
                headers=headers,
                json={**editable, "version": saved["version"]},
            ).status_code
            == 403
        )
    finally:
        auth.get_connection = original


def test_clear_optional_fields_and_consent(client):
    app, http, connect = client
    owner = staff(connect, "agent")
    app.dependency_overrides[require_staff] = lambda: owner
    created = http.post(
        "/staff/properties",
        json={
            "title": "Draft",
            "property_type": "plot",
            "car_parking_spaces": 3,
            "water_notes": "Some note",
            "owner_phone": "private",
            "listing_consent": True,
        },
    )
    endpoint = "/staff/properties/" + created.json()["id"]
    record = http.get(endpoint).json()
    payload = {key: record[key] for key in PropertyDraft.model_fields}
    response = http.put(
        endpoint,
        json={
            **payload,
            "version": record["version"],
            "car_parking_spaces": None,
            "water_notes": None,
            "owner_phone": None,
            "listing_consent": False,
        },
    )
    assert response.status_code == 200, response.text
    saved = http.get(endpoint).json()
    assert saved["car_parking_spaces"] is None and saved["water_notes"] is None
    assert saved["owner_phone"] is None and saved["listing_consent"] is False
    with connect() as connection:
        private = connection.execute(
            "SELECT consent_recorded_at FROM property_private_details WHERE property_id=%s",
            (saved["id"],),
        ).fetchone()
        assert private["consent_recorded_at"] is None


def test_private_write_failure_rolls_back_property_update(client):
    app, http, connect = client
    owner = staff(connect, "agent")
    app.dependency_overrides[require_staff] = lambda: owner
    record = http.post(
        "/staff/properties", json={"title": "Original", "property_type": "flat"}
    ).json()
    endpoint = "/staff/properties/" + record["id"]
    detail = http.get(endpoint).json()
    payload = {key: detail[key] for key in PropertyDraft.model_fields}
    with connect() as connection:
        connection.execute(
            "ALTER TABLE property_private_details ADD CONSTRAINT test_private_failure CHECK (owner_phone IS DISTINCT FROM 'force-failure')"
        )
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            http.put(
                endpoint,
                json={
                    **payload,
                    "version": detail["version"],
                    "title": "Must roll back",
                    "owner_phone": "force-failure",
                },
            )
        unchanged = http.get(endpoint).json()
        assert unchanged["title"] == "Original"
        assert unchanged["version"] == detail["version"]
        assert unchanged["owner_phone"] is None
    finally:
        with connect() as connection:
            connection.execute(
                "ALTER TABLE property_private_details DROP CONSTRAINT test_private_failure"
            )
