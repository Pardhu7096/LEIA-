"""CRM regression tests. Integration tests require an isolated TEST_DATABASE_URL."""

import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

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

    import rate_limits
    original_limits = rate_limits.get_connection
    original = crm.get_connection
    import migrate_crm

    original_migrate = migrate_crm.get_connection
    try:
        # Tests use a dedicated database; explicit guard prevents accidental leia use.
        with connect() as connection:
            name = connection.execute("SELECT current_database() AS name").fetchone()[
                "name"
            ]
            if name not in {"leia_test", "crm_test", "leia_facing_regression_test", "leia_facing_r4_test", "leia_facing_r5_test", "leia_facing_r7_test", "leia_crm_hotfix_test", "leia_builder_test"}:
                pytest.fail("Use an explicitly allowed disposable test database.")
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
        rate_limits.get_connection = connect
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
        rate_limits.get_connection = original_limits
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
            "facing": "northeast",
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
    assert record["facing"] == "northeast"
    with connect() as connection:
        stored = connection.execute(
            "SELECT facing FROM properties WHERE id = %s",
            (property_id,),
        ).fetchone()
        assert stored["facing"] == "northeast"
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
            "facing": "southwest",
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
    assert saved["facing"] == "southwest"

    with connect() as connection:
        stored = connection.execute(
            "SELECT facing FROM properties WHERE id = %s",
            (property_id,),
        ).fetchone()
        assert stored["facing"] == "southwest"

    invalid = http.put(
        endpoint,
        json={
            **{key: saved[key] for key in PropertyDraft.model_fields},
            "version": saved["version"],
            "facing": "invalid",
        },
    )
    assert invalid.status_code == 422

    unchanged = http.get(endpoint).json()
    assert unchanged["facing"] == "southwest"
    assert unchanged["version"] == saved["version"]
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
    # Permission checks above have completed.
    # A submitted property must not be edited, so clearing
    # is tested on a separate draft.
    as_user(owner)
    clearing_draft = http.post(
        "/staff/properties",
        json={
            "title": "Facing clearing test",
            "property_type": "flat",
            "facing": "east",
            "price": 6500000,
            "area_sqft": "1100.25",
        },
    )
    assert clearing_draft.status_code == 201
    clear_path = f"/staff/properties/{clearing_draft.json()['id']}"
    clear_record = http.get(clear_path).json()
    assert clear_record["facing"] == "east"

    cleared = http.put(
        clear_path,
        json={
            **{key: clear_record[key] for key in PropertyDraft.model_fields},
            "version": clear_record["version"],
            "facing": None,
        },
    )
    assert cleared.status_code == 200, cleared.text

    cleared_record = http.get(clear_path).json()
    assert cleared_record["facing"] is None
    assert cleared_record["price"] == clear_record["price"]
    assert cleared_record["area_sqft"] == clear_record["area_sqft"]

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



def test_staff_refresh_rotates_and_keeps_customer_session_separate(client, monkeypatch):
    import auth

    app, http, connect = client
    app.include_router(auth.router)
    monkeypatch.setattr(auth, "get_connection", connect)
    agent = staff(connect, "agent")
    customer = staff(connect, "customer")
    password = "test-password-only"
    with connect() as connection:
        connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        connection.execute(
            "UPDATE users SET password_hash = %s WHERE id IN (%s, %s)",
            (auth.password_hasher.hash(password), agent["id"], customer["id"]),
        )

    def login(path, user):
        return http.post(path, json={"email": user["email"], "password": password})

    csrf = {"X-LEIA-CSRF": "1"}
    assert login("/auth/staff/login", customer).status_code == 403
    assert login("/auth/login", customer).status_code == 200
    customer_cookie = http.cookies.get(auth.COOKIE_NAME)
    assert login("/auth/staff/login", agent).status_code == 200
    staff_cookie = http.cookies.get(auth.STAFF_COOKIE_NAME)
    assert staff_cookie
    assert http.cookies.get(auth.COOKIE_NAME) == customer_cookie
    assert http.post("/auth/staff/refresh").status_code == 403

    refreshed = http.post("/auth/staff/refresh", headers=csrf)
    assert refreshed.status_code == 200
    rotated_cookie = http.cookies.get(auth.STAFF_COOKIE_NAME)
    assert rotated_cookie != staff_cookie
    assert http.get("/staff/properties", headers={
        "Authorization": f"Bearer {refreshed.json()['access_token']}",
    }).status_code == 200

    with TestClient(app) as isolated:
        for replay in (staff_cookie, customer_cookie):
            response = isolated.post("/auth/staff/refresh", headers={
                **csrf, "Cookie": f"{auth.STAFF_COOKIE_NAME}={replay}",
            })
            assert response.status_code == 401

    with connect() as connection:
        connection.execute("UPDATE users SET role = 'customer' WHERE id = %s", (agent["id"],))
    assert http.post("/auth/staff/refresh", headers=csrf).status_code == 403
    assert http.post("/auth/staff/logout", headers=csrf).status_code == 200
    assert http.cookies.get(auth.STAFF_COOKIE_NAME) is None
    assert http.post("/auth/staff/refresh", headers={
        **csrf, "Cookie": f"{auth.STAFF_COOKIE_NAME}={rotated_cookie}",
    }).status_code == 401
    assert http.post("/auth/refresh", headers=csrf).status_code == 200


def test_approved_available_listings_reach_customer_search(database, monkeypatch):
    from types import SimpleNamespace

    import auth
    import groq

    def extract(**kwargs):
        message = SimpleNamespace(
            content=main.SearchRequirements(intent="search", **filters).model_dump_json()
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_groq = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=extract))
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    monkeypatch.setattr(groq, "Groq", lambda **kwargs: fake_groq)
    import main
    import property_media

    # main may already be imported by another test module with a real client;
    # replace it directly so this test never calls the Groq API.
    monkeypatch.setattr(main, "client", fake_groq)
    from io import BytesIO
    from PIL import Image

    connect = database
    monkeypatch.setattr(main, "get_connection", connect)
    monkeypatch.setattr(property_media, "get_connection", connect)
    monkeypatch.setattr(auth, "get_connection", connect)
    owner, other, admin = [staff(connect, role) for role in ("agent", "agent", "admin")]

    def headers(user):
        token = create_access_token(user["id"])["access_token"]
        return {"Authorization": f"Bearer {token}"}

    filters = {"bedrooms": 3, "max_price": 8000000, "location": "Visakhapatnam"}
    with TestClient(main.app) as http:
        payload = {
            "title": "Test Madhura Apartments",
            "property_type": "flat",
            "facing": "southwest",
            "locality": "Madhurawada",
            "city": "Visakhapatnam",
            "price": 7500000,
            "area_sqft": 1130,
            "bedrooms": 3,
            "owner_phone": "private phone",
            "agent_notes": "private notes",
            "listing_consent": False,
        }
        created = http.post("/staff/properties", json=payload, headers=headers(owner))
        assert created.status_code == 201, created.text
        listing = created.json()
        path = f"/staff/properties/{listing['id']}"

        def results():
            response = http.get("/properties", params=filters)
            assert response.status_code == 200, response.text
            return response.json()["properties"]

        assert results() == []
        version = {"version": listing["version"]}
        assert http.post(f"{path}/submit", json=version, headers=headers(other)).status_code == 404
        assert http.post(f"{path}/submit", json=version, headers=headers(owner)).status_code == 422
        assert http.post(f"{path}/approve", json=version, headers=headers(admin)).status_code == 409

        updated = http.put(
            path,
            json={**payload, **version, "listing_consent": True},
            headers=headers(owner),
        )
        assert updated.status_code == 200, updated.text
        def image_bytes(width, height):
            image = Image.new("RGB", (width, height), "green")
            exif = image.getexif()
            exif[0x010E] = "private metadata"
            buffer = BytesIO()
            image.save(buffer, "JPEG", exif=exif)
            return buffer.getvalue()

        version = {"version": updated.json()["version"]}
        cover_path = f"{path}/media/cover"
        panorama_path = f"{path}/media/panorama"
        cover = image_bytes(600, 400)
        panorama = image_bytes(1024, 512)
        assert http.put(cover_path, params=version, content=cover).status_code == 401
        assert http.put(cover_path, params=version, content=cover, headers=headers(other)).status_code == 404
        assert http.put(cover_path, params=version, content=b"<svg/>", headers=headers(owner)).status_code == 422
        assert http.put(cover_path, params=version, content=b"x" * (5 * 1024**2 + 1), headers=headers(owner)).status_code == 413
        assert http.put(panorama_path, params=version, content=cover, headers=headers(owner)).status_code == 422

        uploaded = http.put(cover_path, params=version, content=cover, headers=headers(owner))
        assert uploaded.status_code == 200, uploaded.text
        assert http.delete(cover_path, params=version, headers=headers(owner)).status_code == 409
        version = {"version": uploaded.json()["version"]}
        removed = http.delete(cover_path, params=version, headers=headers(owner))
        assert removed.status_code == 200, removed.text
        assert http.get(cover_path, headers=headers(owner)).status_code == 404
        version = {"version": removed.json()["version"]}
        uploaded = http.put(cover_path, params=version, content=cover, headers=headers(owner))
        assert uploaded.status_code == 200, uploaded.text
        public_cover = f"/property-media/{uploaded.json()['id']}"
        version = {"version": uploaded.json()["version"]}
        uploaded = http.put(panorama_path, params=version, content=panorama, headers=headers(owner))
        assert uploaded.status_code == 200, uploaded.text
        public_tour = f"/property-media/{uploaded.json()['id']}/tour"
        version = {"version": uploaded.json()["version"]}

        assert http.get(public_cover).status_code == 404
        assert http.get(public_tour).status_code == 404
        assert http.get(cover_path, headers=headers(other)).status_code == 404

        gallery = f"{path}/gallery"
        public_gallery = f"/properties/{listing['id']}/gallery"
        assert http.get(gallery).status_code == 401
        assert http.get(gallery, headers=headers(other)).status_code == 404
        assert http.post(gallery, params={**version, "room": "  "}, content=cover, headers=headers(owner)).status_code == 422
        photo_ids = []
        for room in ("Bedroom 1", "bedroom 1", "Kitchen", "Balcony"):
            upload = http.post(
                gallery, params={**version, "room": room}, content=cover, headers=headers(owner),
            )
            assert upload.status_code == 201, upload.text
            assert http.post(
                gallery, params={**version, "room": room}, content=cover, headers=headers(owner),
            ).status_code == 409
            photo_ids.append(upload.json()["id"])
            version = {"version": upload.json()["version"]}

        bedroom, second_bedroom, kitchen, balcony = photo_ids
        assert http.get(public_gallery).status_code == 404
        assert http.get(f"/property-media/{kitchen}").status_code == 404
        assert http.get(f"{gallery}/{kitchen}", headers=headers(other)).status_code == 404
        assert http.get(f"{gallery}/{kitchen}", headers=headers(admin)).status_code == 200
        assert http.put(f"{gallery}/{uuid4()}/cover", params=version, headers=headers(owner)).status_code == 404
        assert http.get(gallery, headers=headers(owner)).json()[0]["is_gallery_cover"]

        def edit_photo(method, suffix):
            response = http.request(method, f"{gallery}/{suffix}", params=version, headers=headers(owner))
            assert response.status_code == 200, response.text
            version.update(version=response.json()["version"])

        edit_photo("PUT", f"{balcony}/cover")
        edit_photo("DELETE", balcony)
        assert http.get(gallery, headers=headers(owner)).json()[0]["is_gallery_cover"]
        edit_photo("PUT", f"{kitchen}/cover")
        assert http.get(gallery, headers=headers(admin)).json() == [
            {"id": bedroom, "room": "Bedroom 1", "is_gallery_cover": False},
            {"id": second_bedroom, "room": "Bedroom 1", "is_gallery_cover": False},
            {"id": kitchen, "room": "Kitchen", "is_gallery_cover": True},
        ]

        submitted = http.post(f"{path}/submit", json=version, headers=headers(owner))
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "submitted"
        assert results() == []
        preview = http.get(cover_path, headers=headers(admin))
        assert preview.status_code == 200, preview.text
        with Image.open(BytesIO(preview.content)) as sanitized:
            assert sanitized.getexif().get(0x010E) is None
        assert http.get(panorama_path, headers=headers(admin)).status_code == 200
        assert http.get(public_cover).status_code == 404
        assert http.get(public_tour).status_code == 404
        assert http.put(
            cover_path, params={"version": submitted.json()["version"]},
            content=cover, headers=headers(admin),
        ).status_code == 409
        assert http.post(f"{path}/approve", json=version, headers=headers(admin)).status_code == 409
        version = {"version": submitted.json()["version"]}
        assert http.post(f"{path}/approve", json=version, headers=headers(owner)).status_code == 403
        approved = http.post(f"{path}/approve", json=version, headers=headers(admin))
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"

        published_record = http.get(
            path, headers=headers(admin)
        ).json()
        assert published_record["facing"] == "southwest"

        with connect() as connection:
            stored = connection.execute(
                "SELECT facing, status FROM properties WHERE id = %s",
                (listing["id"],),
            ).fetchone()
            assert stored["facing"] == "southwest"
            assert stored["status"] == "approved"

        cards = results()
        assert cards == [{
            "id": listing["id"],
            "title": payload["title"],
            "property_type": "flat",
            "facing": "southwest",
            "location": "Madhurawada, Visakhapatnam",
            "price": 7500000,
            "area_sqft": 1130,
            "bedrooms": 3,
            "image": f"/property-media/{kitchen}",
            "tour": public_tour,
            # Migration 014 facts (additive): unrecorded values stay "unknown"/empty, and an
            # unverified RERA number is never published.
            "builder": None, "project_name": None, "construction_status": "unknown",
            "possession_date": None, "floor_number": None, "total_floors": None, "lift": "unknown",
            "gated_community": "unknown", "security": "unknown", "amenities": [],
            "project_source": "unknown", "rera_verified": False, "rera_number": None,
        }]
        assert http.get(public_gallery).json() == [
            {"id": photo_id, "room": room, "image": f"/property-media/{photo_id}"}
            for photo_id, room in zip((bedroom, second_bedroom, kitchen), ("Bedroom 1", "Bedroom 1", "Kitchen"))
        ]
        assert http.get(f"/property-media/{kitchen}").headers["content-type"] == "image/jpeg"
        assert http.get(public_cover).headers["content-type"] == "image/jpeg"
        assert http.get(public_tour).status_code == 200
        assert http.get(public_tour.removesuffix("/tour")).headers["content-type"] == "image/jpeg"

        reply = main.generate_chat_reply(main.ChatRequest(message="3 BHK under 80 lakh in Vizag"))
        assert reply["properties"] == cards
        with connect() as connection:
            connection.execute(
                "UPDATE properties SET availability = 'sold' WHERE id = %s", (listing["id"],)
            )
        assert results() == []
        assert http.get(public_cover).status_code == 404
        assert http.get(public_tour).status_code == 404
        assert http.get(public_gallery).status_code == 404
        assert http.get(f"/property-media/{kitchen}").status_code == 404


def test_nearby_search_uses_property_pin_but_never_returns_it(monkeypatch):
    import io
    import json
    from urllib.parse import parse_qs, urlparse

    import nearby_places

    def google(request, timeout):
        payload = json.loads(request.data)
        assert payload["includedTypes"] == ["bus_stop", "bus_station"]
        assert payload["locationRestriction"]["circle"]["center"] == {
            "latitude": 17.7, "longitude": 83.3,
        }
        assert payload["maxResultCount"] == 5
        assert timeout == 5
        return io.BytesIO(b'{"places":[{"id":"bus-1","displayName":{"text":"Bus stop"}}]}')

    monkeypatch.setattr(
        nearby_places,
        "build_opener",
        lambda *handlers: SimpleNamespace(open=google),
    )
    [place] = nearby_places.find_places(17.7, 83.3, "bus_stops", "test-key")
    assert place["name"] == "Bus stop"
    url = urlparse(place["directions_url"])
    assert url.hostname == "www.google.com"
    # GEO-M1.1 (M1): the pin is used server-side for the search but never returned.
    assert "origin" not in parse_qs(url.query)
    assert "17.7" not in place["directions_url"] and "83.3" not in place["directions_url"]
    assert "17.7" not in json.dumps(place) and "83.3" not in json.dumps(place)
    assert parse_qs(url.query)["destination_place_id"] == ["bus-1"]


def test_temple_chat_returns_shortest_walk_without_saving_map_data(monkeypatch):
    import rate_limits
    monkeypatch.setattr(rate_limits, "consume", lambda *args: None)
    import importlib
    import io
    import json
    from unittest.mock import MagicMock

    import nearby_places

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-places-key")
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "test-routes-key")
    for name in os.environ:
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    main = importlib.import_module("main")
    conversation_id, user_id, first_id, second_id = (uuid4() for _ in range(4))
    filters = main.SearchFilters(location="Madhurawada").model_dump()
    cursor = MagicMock()
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(main, "get_connection", lambda: connection)
    calls = []

    def google(request, timeout):
        payload = json.loads(request.data)
        calls.append(request.full_url)
        if len(calls) == 1:
            assert payload["includedTypes"] == ["hindu_temple"]
            return io.BytesIO(b'{"places":[{"id":"first","displayName":{"text":"First temple"}},'
                              b'{"id":"second","displayName":{"text":"Shiva temple"}}]}')

        assert request.full_url == "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
        assert request.get_header("X-goog-api-key") == "test-routes-key"
        assert payload["travelMode"] == "WALK"
        assert payload["origins"][0]["waypoint"]["location"]["latLng"] == {
            "latitude": 17.7, "longitude": 83.3,
        }
        assert payload["destinations"] == [
            {"waypoint": {"placeId": "first"}}, {"waypoint": {"placeId": "second"}},
        ]
        return io.BytesIO(b'[{"destinationIndex":1,"condition":"ROUTE_EXISTS","status":{},'
                          b'"distanceMeters":900,"duration":"780s"},'
                          b'{"condition":"ROUTE_EXISTS","status":{},'
                          b'"distanceMeters":1200,"duration":"900s"}]')

    monkeypatch.setattr(
        nearby_places,
        "build_opener",
        lambda *handlers: SimpleNamespace(open=google),
    )
    auth_override = {main.get_current_user: lambda: {"id": user_id}}
    monkeypatch.setattr(main.app, "dependency_overrides", auth_override)
    properties = [{"id": str(first_id)}, {"id": str(second_id)}]
    with TestClient(main.app) as http:
        cursor.fetchone.side_effect = [
            {"filters": filters}, {"properties": properties},
            {"filters": filters}, {"user_message_id": 1, "assistant_message_id": 2},
        ]
        response = http.post("/chat", json={
            "message": "How far is the nearest temple?", "conversation_id": str(conversation_id),
        })
        assert response.status_code == 200
        assert "Which listing" in response.json()["reply"]
        assert not calls

        cursor.reset_mock()
        cursor.fetchone.side_effect = [
            {"filters": filters}, {"properties": properties},
            {"title": "SS Castle", "latitude": 17.7, "longitude": 83.3},
            {"filters": filters}, {"user_message_id": 3, "assistant_message_id": 4},
        ]
        response = http.post("/chat", json={
            "message": "How far is the nearest temple from listing 2?",
            "conversation_id": str(conversation_id),
        })

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert "Shiva temple · 900 m · about 13 minutes on foot" in result["reply"]
    assert result["filters"] == filters
    queries = [call.args for call in cursor.execute.call_args_list]
    assert queries[2][1] == (second_id,)
    assert "FOR UPDATE" in queries[3][0]  # Google was called between the two database phases
    assert queries[4][1]["reply"] == "Ask again to get a fresh walking estimate from Google Maps."
    assert "Shiva temple" not in json.dumps(queries[4][1], default=str)
    assert len(calls) == 2


def test_selected_property_facts_and_walk_use_only_that_listing(monkeypatch):
    import rate_limits
    import json
    monkeypatch.setattr(rate_limits, "consume", lambda *args: None)
    import importlib
    from unittest.mock import MagicMock

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    for name in os.environ:
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    main = importlib.import_module("main")
    conversation_id, user_id, property_id = (uuid4() for _ in range(3))
    filters = main.SearchFilters(location="Madhurawada").model_dump()
    listing = {
        "title": "SS Castle", "latitude": 17.7, "longitude": 83.3,
        "area_sqft": 2200, "price": 8900000, "bedrooms": 3, "bathrooms": None,
        "location": "Madhurawada", "property_type": "independent_house",
        "facing": "northeast",
        "car_parking_spaces": 0, "maintenance_monthly": None,
    }
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(main, "get_connection", lambda: connection)
    monkeypatch.setattr(main.app, "dependency_overrides", {
        main.get_current_user: lambda: {"id": user_id},
    })
    walk = MagicMock(return_value="Test Hospital · 450 m · about 6 minutes on foot.")
    monkeypatch.setattr(main, "shortest_nearby_walk", walk)
    search = MagicMock(side_effect=AssertionError("Selected questions must not run a property search"))
    monkeypatch.setattr(main, "generate_chat_reply", search)

    with TestClient(main.app) as http:
        def ask(message, selected_listing=listing):
            cursor.reset_mock()
            cursor.fetchone.side_effect = [
                {"filters": filters}, selected_listing, {"filters": filters}, {"user_message_id": 1, "assistant_message_id": 2},
            ]
            response = http.post("/chat", json={
                "message": message, "conversation_id": str(conversation_id),
                "property_id": str(property_id),
            })
            assert response.status_code == 200
            assert cursor.execute.call_args_list[1].args[1] == (property_id,)
            assert "status = 'approved'" in cursor.execute.call_args_list[1].args[0]
            assert response.json()["filters"] == filters
            return response.json()["reply"]

        assert "Area: 2,200 sq ft" in ask("How big is it?")
        assert "Facing: Northeast" in ask("Which direction does this house face?")
        assert "northeast-facing" in ask("Is this house northeast-facing?")
        assert "not east-facing" in ask("Is this house east-facing?")
        assert "northeast-facing" in ask("Is this house north-east facing?")
        assert "Facing: Not listed" in ask(
            "Which direction does this house face?",
            {**listing, "facing": None},
        )
        assert "has not been specified" in ask(
            "Is this house north-facing?",
            {**listing, "facing": None},
        )
        assert "Bathrooms: Not listed" in ask("How many bathrooms does it have?")
        assert "Car parking spaces: 0" in ask("Does it have parking?")
        assert "I don’t have that detail for SS Castle" in ask("Does it have a balcony?")
        assert "no longer available" in ask("How big is it?", None)
        assert "450 m" in ask("How far is the nearest hospital?")
        walk.assert_called_once_with(17.7, 83.3, "hospitals")
        assert "FOR UPDATE" in cursor.execute.call_args_list[2].args[0]
        save = cursor.execute.call_args_list[3].args
        assert save[1]["reply"] == "Ask again to get a fresh walking estimate from Google Maps."
        assert "450 m" not in json.dumps(save[1], default=str)
        # Both saved messages (user and assistant rows) carry the selected property.
        assert save[1]["property_id"] == property_id and save[0].count("%(property_id)s") == 2

        cursor.reset_mock()
        cursor.fetchone.side_effect = [{"id": conversation_id, "title": "Test", "filters": filters}]
        cursor.fetchall.return_value = [
            {"id": 1, "role": "user", "content": "Show homes", "properties": [], "property_id": None},
            {"id": 2, "role": "user", "content": "How big is it?", "properties": [], "property_id": property_id},
        ]
        restored = http.get(f"/conversations/{conversation_id}")
        assert restored.status_code == 200
        assert "property_id" in cursor.execute.call_args_list[1].args[0]
        assert [message["property_id"] for message in restored.json()["messages"]] == [None, str(property_id)]
    search.assert_not_called()


def test_shared_rate_limits_and_recovery(database, monkeypatch):
    import rate_limits
    from fastapi import HTTPException
    from concurrent.futures import ThreadPoolExecutor

    identity = str(uuid4())
    def attempt(_):
        try:
            rate_limits.consume("test-concurrent", identity, 3, 60)
            return 200
        except HTTPException as error:
            assert error.status_code == 429
            assert 1 <= int(error.headers["Retry-After"]) <= 60
            return 429

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert outcomes.count(200) == 3
    assert outcomes.count(429) == 5
    rate_limits.consume("test-concurrent", str(uuid4()), 3, 60)
    with database() as connection:
        connection.execute("UPDATE public.request_limits SET expires_at = clock_timestamp() - interval '1 second'")
    assert attempt(None) == 200

    def unavailable():
        raise OSError("database unavailable")
    monkeypatch.setattr(rate_limits, "get_connection", unavailable)
    with pytest.raises(HTTPException) as caught:
        rate_limits.consume("test", identity, 3, 60)
    assert caught.value.status_code == 503


def test_public_auth_limit_rejects_before_login_work(monkeypatch):
    import auth
    import rate_limits
    from fastapi import HTTPException
    app = FastAPI()
    app.include_router(auth.router)
    def blocked(*args):
        raise HTTPException(429, "Limited", headers={"Retry-After": "60"})
    monkeypatch.setattr(rate_limits, "consume", blocked)
    def unexpected(*args, **kwargs):
        raise AssertionError("Authentication database must not be called")
    monkeypatch.setattr(auth, "get_connection", unexpected)
    with TestClient(app) as http:
        for path in ("/auth/login", "/auth/staff/login", "/auth/register"):
            result = http.post(path, json={"email": "test@example.com", "password": "test-password-only", "name": "Test"})
            assert result.status_code == 429
            assert result.headers["retry-after"] == "60"


def test_paid_routes_reject_before_external_or_database_work(monkeypatch):
    import importlib
    import rate_limits
    import nearby_places
    from fastapi import HTTPException
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    main = importlib.import_module("main")
    def blocked(*args):
        raise HTTPException(429, "Limited", headers={"Retry-After": "60"})
    monkeypatch.setattr(rate_limits, "consume", blocked)
    user = {"id": uuid4()}
    monkeypatch.setattr(main.app, "dependency_overrides", {
        main.get_current_user: lambda: user,
    })
    def unexpected(*args, **kwargs):
        raise AssertionError("Limited request reached database or paid provider")
    monkeypatch.setattr(main, "get_connection", unexpected)
    monkeypatch.setattr(nearby_places, "get_connection", unexpected)
    monkeypatch.setattr(main, "generate_chat_reply", unexpected)
    monkeypatch.setattr(nearby_places, "find_places", unexpected)
    with TestClient(main.app) as http:
        response = http.post("/chat", json={"message": "Show homes", "conversation_id": str(uuid4())})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        response = http.get(f"/properties/{uuid4()}/nearby?category=schools")
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"

@pytest.fixture(scope="module")
def facing_search_records(database):
    connect = database
    owner = staff(connect, "agent")

    directions = (
        "north", "northeast", "east", "southeast",
        "south", "southwest", "west", "northwest",
    )

    city = f"FacingTest-{uuid4().hex}"
    records = {}

    def insert(key, facing, status="approved",
               availability="available", price=6500000,
               property_type="flat", bedrooms=3,
               locality="TestLocality"):
        property_id = uuid4()

        with connect() as connection:
            connection.execute(
                """
                INSERT INTO properties (
                    id, created_by, title, property_type,
                    facing, price, bedrooms, area_sqft,
                    locality, city, status, availability,
                    reviewed_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, 1500,
                    %s, %s, %s, %s,
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    property_id, owner["id"],
                    f"Facing test {key}", property_type,
                    facing, price, bedrooms,
                    locality, city, status, availability,
                ),
            )

        records[key] = str(property_id)

    for direction in directions:
        insert(direction, direction)

    insert("unspecified", None)
    insert("draft", "north", status="draft")
    insert("submitted", "north", status="submitted")
    insert("rejected", "north", status="rejected")
    insert("archived", "north", status="archived")
    insert("sold", "north", availability="sold")
    insert("withdrawn", "north", availability="withdrawn")

    insert(
        "expensive_north", "north",
        price=9500000,
        locality="PremiumLocality",
        property_type="independent_house",
        bedrooms=4,
    )

    yield city, records


@pytest.mark.parametrize(
    "direction",
    [
        "north", "northeast", "east", "southeast",
        "south", "southwest", "west", "northwest",
    ],
)
def test_facing_search_integration(
    database, facing_search_records, monkeypatch, direction
):
    import main

    monkeypatch.setattr(main, "get_connection", database)

    city, records = facing_search_records

    with TestClient(main.app) as http:
        response = http.get(
            "/properties",
            params={"location": city, "facing": direction},
        )

    assert response.status_code == 200, response.text

    cards = response.json()["properties"]
    expected = {records[direction]}

    if direction == "north":
        expected.add(records["expensive_north"])

    assert {card["id"] for card in cards} == expected
    assert all(card["facing"] == direction for card in cards)


@pytest.mark.parametrize(
    "extra_filters, expected_keys",
    [
        (
            {"facing": "north", "max_price": 8000000},
            ["north"],
        ),
        (
            {"facing": "north", "min_price": 8000000},
            ["expensive_north"],
        ),
        (
            {"facing": "north", "property_type": "flat"},
            ["north"],
        ),
        (
            {"facing": "north",
             "property_type": "independent_house"},
            ["expensive_north"],
        ),
        (
            {"facing": "north", "bedrooms": 3},
            ["north"],
        ),
        (
            {"facing": "north", "bedrooms": 4},
            ["expensive_north"],
        ),
        (
            {"facing": "east", "max_price": 5000000},
            [],
        ),
        (
            {"facing": "north", "property_type": "plot"},
            [],
        ),
        (
            {
                "facing": "north",
                "bedrooms": 3,
                "max_price": 8000000,
                "property_type": "flat",
            },
            ["north"],
        ),
        (
            {},
            [
                "north", "northeast", "east", "southeast",
                "south", "southwest", "west", "northwest",
                "unspecified", "expensive_north",
            ],
        ),
    ],
)
def test_combined_facing_search_filters(
    database, facing_search_records,
    monkeypatch, extra_filters, expected_keys
):
    import main

    monkeypatch.setattr(main, "get_connection", database)

    city, records = facing_search_records

    with TestClient(main.app) as http:
        response = http.get(
            "/properties",
            params={"location": city, **extra_filters},
        )

    assert response.status_code == 200, response.text

    cards = response.json()["properties"]

    assert {card["id"] for card in cards} == {
        records[key] for key in expected_keys
    }

    if "unspecified" in expected_keys:
        unknown = next(
            card for card in cards
            if card["id"] == records["unspecified"]
        )
        assert unknown["facing"] is None


@pytest.mark.parametrize(
    "direction",
    ["invalid", "unknown", "north-east", "East", "N"],
)
def test_invalid_facing_search_request(
    database, monkeypatch, direction
):
    import main

    def forbidden_connection():
        pytest.fail("Invalid Facing reached the database")

    monkeypatch.setattr(
        main, "get_connection", forbidden_connection
    )

    with TestClient(main.app) as http:
        response = http.get(
            "/properties", params={"facing": direction}
        )

    assert response.status_code == 422, response.text



@pytest.fixture
def saved_chat_setup(database, facing_search_records, monkeypatch):
    import json
    import main
    import auth
    from unittest.mock import Mock

    monkeypatch.setattr(main, "get_connection", database)
    monkeypatch.setattr(auth, "get_connection", database)

    # Rate limiting has independent regression coverage.
    monkeypatch.setattr(main, "limit_chat", lambda user: None)
    # These scenarios cover the AI-first path (mocked provider), still through the cost guard.
    import ai_guard
    import rate_limits
    monkeypatch.setenv("LEIA_SEARCH_ROUTER", "ai")
    monkeypatch.setattr(ai_guard, "get_connection", database)
    monkeypatch.setattr(rate_limits, "get_connection", database)

    customer = staff(database, "customer")
    stranger = staff(database, "customer")

    def headers(user):
        token = create_access_token(user["id"])["access_token"]
        return {"Authorization": f"Bearer {token}"}

    ai = Mock()
    monkeypatch.setattr(
        main.client.chat.completions, "create", ai
    )

    def respond(payload):
        content = (
            payload if isinstance(payload, str)
            else json.dumps(payload)
        )

        ai.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content)
                )
            ]
        )

    city, records = facing_search_records

    with TestClient(main.app) as http:
        owner_headers = headers(customer)
        other_headers = headers(stranger)

        created = http.post(
            "/conversations",
            headers=owner_headers,
        )
        assert created.status_code in (200, 201), created.text

        conversation_id = created.json()["id"]

        yield SimpleNamespace(
            main=main,
            http=http,
            connect=database,
            headers=owner_headers,
            other_headers=other_headers,
            conversation_id=conversation_id,
            path=f"/conversations/{conversation_id}",
            ai=ai,
            respond=respond,
            city=city,
            records=records,
        )


def send_saved_chat(ctx, message, payload):
    import json

    ctx.respond(payload)

    response = ctx.http.post(
        "/chat",
        headers=ctx.headers,
        json={
            "message": message,
            "conversation_id": ctx.conversation_id,

            # Client-supplied filters must not override saved filters.
            "filters": {"facing": "south"},
        },
    )

    assert response.status_code == 200, response.text

    result = response.json()

    reopened = ctx.http.get(
        ctx.path, headers=ctx.headers
    )
    assert reopened.status_code == 200, reopened.text
    saved = reopened.json()

    assert saved["filters"] == result["filters"]
    assert saved["messages"][-2]["role"] == "user"
    assert saved["messages"][-2]["content"] == message
    assert saved["messages"][-1]["role"] == "assistant"
    assert saved["messages"][-1]["properties"] == result["properties"]

    with ctx.connect() as connection:
        stored = connection.execute(
            "SELECT filters FROM conversations WHERE id = %s",
            (ctx.conversation_id,),
        ).fetchone()

    assert stored["filters"] == result["filters"]

    ai_input = json.loads(
        ctx.ai.call_args.kwargs["messages"][-1]["content"]
    )

    return result, ai_input


def seed_saved_chat(ctx):
    result, ai_input = send_saved_chat(
        ctx,
        "Find northeast facing flats under 80 lakh",
        {
            "intent": "search",
            "location": ctx.city,
            "bedrooms": 3,
            "property_type": "flat",
            "max_price": 8000000,
            "facing": "northeast",
        },
    )

    assert ai_input["current_filters"] == (
        ctx.main.SearchFilters().model_dump()
    )

    assert {
        row["id"] for row in result["properties"]
    } == {ctx.records["northeast"]}

    return result["filters"]


def test_saved_facing_conversation_multiturn(saved_chat_setup):
    ctx = saved_chat_setup
    previous = seed_saved_chat(ctx)

    result, ai_input = send_saved_chat(
        ctx,
        "Increase budget to 1 crore",
        {"intent": "search", "max_price": 10000000},
    )

    assert ai_input["current_filters"] == previous
    expected = {**previous, "max_price": 10000000}
    assert result["filters"] == expected
    assert {
        row["id"] for row in result["properties"]
    } == {ctx.records["northeast"]}

    result, ai_input = send_saved_chat(
        ctx,
        "Change facing to east",
        {"intent": "search", "facing": "east"},
    )

    assert ai_input["current_filters"] == expected
    expected["facing"] = "east"
    assert result["filters"] == expected

    assert {
        row["id"] for row in result["properties"]
    } == {ctx.records["east"]}

    result, ai_input = send_saved_chat(
        ctx,
        "Any facing is fine",
        {"intent": "search", "facing": None},
    )

    assert ai_input["current_filters"] == expected
    expected["facing"] = None
    assert result["filters"] == expected

    assert {
        row["id"] for row in result["properties"]
    } == {
        ctx.records[key]
        for key in (
            "north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest",
            "unspecified",
        )
    }


def test_saved_facing_conversation_no_matches(saved_chat_setup):
    ctx = saved_chat_setup
    previous = seed_saved_chat(ctx)

    result, _ = send_saved_chat(
        ctx,
        "Reduce budget to 50 lakh",
        {"intent": "search", "max_price": 5000000},
    )

    assert result["properties"] == []
    assert result["reply"] == ctx.main.NO_MATCHES_REPLY
    assert result["filters"] == {
        **previous, "max_price": 5000000
    }


def test_saved_facing_conversation_clarify(saved_chat_setup):
    ctx = saved_chat_setup
    previous = seed_saved_chat(ctx)

    result, _ = send_saved_chat(
        ctx,
        "East or north facing",
        {
            "intent": "clarify",
            "question": "Which direction do you prefer?",
        },
    )

    assert result["filters"] == previous
    assert result["properties"] == []
    assert result["reply"] == "Which direction do you prefer?"


def test_saved_facing_conversation_reset(saved_chat_setup):
    ctx = saved_chat_setup
    seed_saved_chat(ctx)

    result, _ = send_saved_chat(
        ctx,
        "Start over",
        {"intent": "reset"},
    )

    assert result["filters"] == (
        ctx.main.SearchFilters().model_dump()
    )
    assert result["properties"] == []


@pytest.mark.parametrize(
    "invalid_response",
    [
        "not valid JSON",
        '{"intent":"search","facing":"invalid"}',
    ],
)
def test_saved_facing_conversation_invalid_response_rollback(
    saved_chat_setup, invalid_response
):
    ctx = saved_chat_setup
    seed_saved_chat(ctx)

    before = ctx.http.get(
        ctx.path, headers=ctx.headers
    ).json()

    ctx.respond(invalid_response)

    response = ctx.http.post(
        "/chat",
        headers=ctx.headers,
        json={
            "message": "Update my search",
            "conversation_id": ctx.conversation_id,
        },
    )

    assert response.status_code == 502, response.text

    after = ctx.http.get(
        ctx.path, headers=ctx.headers
    ).json()

    assert after == before


def test_saved_facing_conversation_ownership(saved_chat_setup):
    ctx = saved_chat_setup
    seed_saved_chat(ctx)

    before = ctx.http.get(
        ctx.path, headers=ctx.headers
    ).json()

    ctx.ai.reset_mock()

    response = ctx.http.get(
        ctx.path, headers=ctx.other_headers
    )
    assert response.status_code == 404

    response = ctx.http.post(
        "/chat",
        headers=ctx.other_headers,
        json={
            "message": "Change facing to west",
            "conversation_id": ctx.conversation_id,
        },
    )
    assert response.status_code == 404

    response = ctx.http.delete(
        ctx.path, headers=ctx.other_headers
    )
    assert response.status_code == 404

    ctx.ai.assert_not_called()

    after = ctx.http.get(
        ctx.path, headers=ctx.headers
    ).json()

    assert after == before


def test_native_refresh_concurrency_and_reuse_on_postgres(client, monkeypatch):
    """Migration 009 + SELECT ... FOR UPDATE under real concurrent requests."""
    import threading

    import auth
    import native_sessions

    app, _, connect = client
    app.include_router(native_sessions.router)
    monkeypatch.setattr(auth, "get_connection", connect)
    monkeypatch.setattr(native_sessions, "get_connection", connect)
    password = "native-test-password"

    with connect() as connection:
        columns = {
            row["column_name"] for row in connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'auth_sessions'"""
            ).fetchall()
        }
        assert {"previous_refresh_token_hash", "rotation_request_hash",
                "rotated_at", "revoke_reason"} <= columns
        connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")

    def native_user():
        user = staff(connect, "customer")
        with connect() as connection:
            connection.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (auth.password_hasher.hash(password), user["id"]),
            )
        with TestClient(app) as http:
            response = http.post("/auth/native/login",
                                 json={"email": user["email"], "password": password})
        assert response.status_code == 200, response.text
        return user, response.json()["refresh_token"]

    def race(token, request_ids):
        barrier = threading.Barrier(len(request_ids))
        results = [None] * len(request_ids)

        def worker(index, request_id):
            with TestClient(app) as http:
                barrier.wait()
                results[index] = http.post("/auth/native/refresh", json={
                    "refresh_token": token, "request_id": request_id,
                })

        threads = [threading.Thread(target=worker, args=(i, rid))
                   for i, rid in enumerate(request_ids)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def session_of(user):
        with connect() as connection:
            return connection.execute(
                "SELECT revoked_at, revoke_reason FROM auth_sessions WHERE user_id = %s",
                (user["id"],),
            ).fetchone()

    # Same attempt retried concurrently: both succeed with the identical token.
    user, token = native_user()
    same = "a" * 32
    first, second = race(token, [same, same])
    assert first.status_code == second.status_code == 200
    assert first.json()["refresh_token"] == second.json()["refresh_token"]
    assert session_of(user)["revoked_at"] is None

    # Two different holders of one token: one wins, the reuse revokes the session.
    user, token = native_user()
    results = race(token, ["b" * 32, "c" * 32])
    assert sorted(r.status_code for r in results) == [200, 401]
    assert session_of(user)["revoke_reason"] == "reuse_detected"


# ---------------------------------------------------------------------------
# GEO-M1: on-demand GPS capture provenance
# ---------------------------------------------------------------------------

GPS_TIME = "2026-11-20T05:00:00+00:00"  # fixed past time (future times are rejected)


@pytest.mark.parametrize(
    "changes, ok",
    [
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "gps",
          "location_accuracy_m": 12.5, "location_captured_at": GPS_TIME}, True),
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "manual"}, True),
        ({"latitude": 17.7, "longitude": 83.3}, True),  # older client: no metadata
        ({}, True),
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "gps",
          "location_captured_at": GPS_TIME}, False),  # GPS without accuracy
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "gps",
          "location_accuracy_m": 12.5}, False),  # GPS without time
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "gps",
          "location_accuracy_m": 150, "location_captured_at": GPS_TIME}, False),  # too imprecise
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "gps",
          "location_accuracy_m": 10, "location_captured_at": "2099-01-01T00:00:00Z"}, False),  # future
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "manual",
          "location_accuracy_m": 10}, False),  # manual with GPS data
        ({"latitude": 17.7, "longitude": 83.3, "location_accuracy_m": 10}, False),  # no source
        ({"location_source": "manual"}, False),  # metadata without coordinates
        ({"latitude": 17.7, "longitude": 83.3, "location_source": "network"}, False),
    ],
)
def test_location_capture_validation(changes, ok):
    payload = {"title": "Home", "property_type": "flat", **changes}
    if ok:
        PropertyDraft(**payload)
    else:
        with pytest.raises(ValidationError):
            PropertyDraft(**payload)


def test_gps_location_survives_edit_submit_and_approval(database, monkeypatch):
    import auth
    import groq
    import main
    import property_media

    fake_groq = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=None)))
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    monkeypatch.setattr(groq, "Groq", lambda **kwargs: fake_groq)
    monkeypatch.setattr(main, "client", fake_groq)
    connect = database
    monkeypatch.setattr(main, "get_connection", connect)
    monkeypatch.setattr(property_media, "get_connection", connect)
    monkeypatch.setattr(auth, "get_connection", connect)
    owner, admin = [staff(connect, role) for role in ("agent", "admin")]

    def headers(user):
        return {"Authorization": f"Bearer {create_access_token(user['id'])['access_token']}"}

    def stored(property_id):
        with connect() as connection:
            return connection.execute(
                """SELECT latitude, longitude, location_source, location_accuracy_m,
                          location_captured_at, status FROM properties WHERE id = %s""",
                (property_id,),
            ).fetchone()

    gps = {"latitude": 17.744821, "longitude": 83.341207, "location_source": "gps",
           "location_accuracy_m": 8.4, "location_captured_at": GPS_TIME}
    base = {
        "title": "GPS Pinned Villa", "property_type": "flat", "locality": "Madhurawada",
        "city": "Visakhapatnam", "price": 6400000, "area_sqft": 1210, "bedrooms": 2,
        "owner_name": "Private owner", "owner_phone": "+91 90000 00000",
        "listing_consent": True,
    }
    with TestClient(main.app) as http:
        created = http.post("/staff/properties", json={**base, **gps}, headers=headers(owner))
        assert created.status_code == 201, created.text
        listing = created.json()
        path = f"/staff/properties/{listing['id']}"
        detail = http.get(path, headers=headers(owner)).json()
        assert detail["location_source"] == "gps"
        assert detail["location_accuracy_m"] == pytest.approx(8.4)
        # Same instant regardless of the database session's time zone.
        from datetime import datetime
        assert datetime.fromisoformat(detail["location_captured_at"]) == datetime.fromisoformat(GPS_TIME)
        row = stored(listing["id"])
        assert (row["latitude"], row["longitude"]) == (17.744821, 83.341207)

        # Agent corrects the pin by hand: provenance becomes manual, GPS data cleared.
        manual = {"latitude": 17.7449, "longitude": 83.3413, "location_source": "manual"}
        edited = http.put(path, json={**base, **manual, "version": detail["version"]}, headers=headers(owner))
        assert edited.status_code == 200, edited.text
        row = stored(listing["id"])
        assert row["location_source"] == "manual"
        assert row["location_accuracy_m"] is None and row["location_captured_at"] is None

        # Recapture by GPS.
        version = http.get(path, headers=headers(owner)).json()["version"]
        recaptured = http.put(path, json={**base, **gps, "version": version}, headers=headers(owner))
        assert recaptured.status_code == 200, recaptured.text

        # GEO-M1.1 B3: the installed (older) Agent APK omits the location fields.
        # Editing an unrelated field with the same coordinates keeps the GPS metadata.
        gps_row = stored(listing["id"])
        version = http.get(path, headers=headers(owner)).json()["version"]
        legacy_body = {**base, "price": 6500000, "latitude": 17.744821, "longitude": 83.341207, "version": version}
        legacy = http.put(path, json=legacy_body, headers=headers(owner))
        assert legacy.status_code == 200, legacy.text
        row = stored(listing["id"])
        for key in ("latitude", "longitude", "location_source", "location_accuracy_m", "location_captured_at"):
            assert row[key] == gps_row[key], key
        assert row["location_source"] == "gps"
        assert legacy.json()["price"] == 6500000

        # Older APK changes the coordinates (typed): manual, GPS accuracy and time cleared.
        version = legacy.json()["version"]
        moved = http.put(path, json={**base, "latitude": 17.75, "longitude": 83.35, "version": version}, headers=headers(owner))
        assert moved.status_code == 200, moved.text
        row = stored(listing["id"])
        assert (row["location_source"], row["location_accuracy_m"], row["location_captured_at"]) == ("manual", None, None)

        # Older APK clears the coordinates: no provenance left.
        version = moved.json()["version"]
        cleared = http.put(path, json={**base, "version": version}, headers=headers(owner))
        assert cleared.status_code == 200, cleared.text
        row = stored(listing["id"])
        assert (row["latitude"], row["location_source"], row["location_accuracy_m"]) == (None, None, None)

        # Explicit nulls from a GEO-M1 client are honoured (not treated as "omitted").
        version = cleared.json()["version"]
        explicit = http.put(
            path,
            json={**base, "latitude": 17.744821, "longitude": 83.341207, "location_source": None,
                  "location_accuracy_m": None, "location_captured_at": None, "version": version},
            headers=headers(owner),
        )
        assert explicit.status_code == 200, explicit.text
        assert stored(listing["id"])["location_source"] is None

        # Final GPS save, then submit and approve: nothing about the location changes.
        version = http.get(path, headers=headers(owner)).json()["version"]
        assert http.put(path, json={**base, **gps, "version": version}, headers=headers(owner)).status_code == 200
        before = stored(listing["id"])
        version = http.get(path, headers=headers(owner)).json()["version"]
        submitted = http.post(f"{path}/submit", json={"version": version}, headers=headers(owner))
        assert submitted.status_code == 200, submitted.text
        approved = http.post(f"{path}/approve", json={"version": submitted.json()["version"]}, headers=headers(admin))
        assert approved.status_code == 200, approved.text
        after = stored(listing["id"])
        assert after["status"] == "approved"
        for key in ("latitude", "longitude", "location_source", "location_accuracy_m", "location_captured_at"):
            assert after[key] == before[key], key

        # Published listing: no coordinates or location metadata in the public search payload.
        public = http.get("/properties").json()["properties"]
        mine = [p for p in public if p["id"] == listing["id"]]
        assert mine, "approved listing should be searchable"
        text = repr(mine[0])
        for secret in ("17.744821", "83.341207", "location_source", "location_accuracy", "latitude"):
            assert secret not in text, secret

        # Digital Studio snapshot excludes the provenance metadata.
        with connect() as connection:
            job = connection.execute(
                "SELECT property_snapshot FROM marketing_jobs WHERE property_id = %s", (listing["id"],)
            ).fetchone()
        assert job is not None, "approval should queue a marketing job"
        snapshot_property = job["property_snapshot"]["property"]
        for key in ("location_source", "location_accuracy_m", "location_captured_at"):
            assert key not in snapshot_property, key


def test_migration_011_constraints_reject_inconsistent_rows(database):
    connect = database
    owner = staff(connect, "agent")
    with connect() as connection:
        connection.execute(
            "INSERT INTO properties (title, property_type, created_by) VALUES ('Constraint probe', 'flat', %s)",
            (owner["id"],),
        )
    bad_updates = [
        "location_source = 'manual'",  # metadata without coordinates
        "latitude = 1, longitude = 1, location_source = 'gps'",  # gps without accuracy/time
        "latitude = 1, longitude = 1, location_source = 'manual', location_accuracy_m = 5",
        "latitude = 1, longitude = 1, location_source = 'satellite'",
        "latitude = 1, longitude = 1, location_source = 'gps', location_accuracy_m = 9999, location_captured_at = now()",
    ]
    for assignment in bad_updates:
        with pytest.raises(psycopg.errors.CheckViolation):
            with connect() as connection:
                connection.execute(f"UPDATE properties SET {assignment} WHERE title = 'Constraint probe'")


# ---------------------------------------------------------------------------
# GEO-M1.1: legacy clients, concurrency and device clock
# ---------------------------------------------------------------------------


def test_legacy_create_classifies_typed_coordinates_as_manual(client):
    app, http, connect = client
    owner = staff(connect, "agent")
    app.dependency_overrides[require_staff] = lambda: owner
    with_pin = http.post("/staff/properties", json={"title": "Legacy pin", "property_type": "flat",
                                                    "latitude": 17.7, "longitude": 83.3})
    no_pin = http.post("/staff/properties", json={"title": "Legacy no pin", "property_type": "flat"})
    assert with_pin.status_code == no_pin.status_code == 201
    with connect() as connection:
        rows = {r["title"]: r for r in connection.execute(
            "SELECT title, location_source, location_accuracy_m FROM properties WHERE title LIKE 'Legacy %'").fetchall()}
    assert (rows["Legacy pin"]["location_source"], rows["Legacy pin"]["location_accuracy_m"]) == ("manual", None)
    assert rows["Legacy no pin"]["location_source"] is None


def test_concurrent_legacy_saves_keep_version_protection_and_gps_metadata(database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import auth
    import main
    import property_media

    connect = database
    monkeypatch.setattr(main, "get_connection", connect)
    monkeypatch.setattr(property_media, "get_connection", connect)
    monkeypatch.setattr(auth, "get_connection", connect)
    owner = staff(connect, "agent")
    token = {"Authorization": f"Bearer {create_access_token(owner['id'])['access_token']}"}
    gps = {"title": "Concurrent", "property_type": "flat", "latitude": 17.71, "longitude": 83.31,
           "location_source": "gps", "location_accuracy_m": 6.0, "location_captured_at": GPS_TIME}
    with TestClient(main.app) as http:
        created = http.post("/staff/properties", json=gps, headers=token)
        assert created.status_code == 201, created.text
        path = f"/staff/properties/{created.json()['id']}"
        version = http.get(path, headers=token).json()["version"]

    def legacy_save(price):
        body = {"title": "Concurrent", "property_type": "flat", "price": price,
                "latitude": 17.71, "longitude": 83.31, "version": version}
        with TestClient(main.app) as worker:
            return worker.put(path, json=body, headers=token)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(legacy_save, [1000001, 2000002]))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r for r in results if r.status_code == 200).json()
    with connect() as connection:
        row = connection.execute(
            "SELECT price, version, location_source, location_accuracy_m, location_captured_at FROM properties WHERE id = %s",
            (created.json()["id"],),
        ).fetchone()
    assert row["price"] == winner["price"] and row["version"] == version + 1
    assert (row["location_source"], row["location_accuracy_m"]) == ("gps", 6.0)
    assert row["location_captured_at"] is not None


def test_gps_capture_time_clock_rules():
    from datetime import datetime, timedelta, timezone

    def draft(captured):
        return {"title": "Clock", "property_type": "flat", "latitude": 17.7, "longitude": 83.3,
                "location_source": "gps", "location_accuracy_m": 9, "location_captured_at": captured}

    now = datetime.now(timezone.utc)
    stored = PropertyDraft(**draft((now + timedelta(minutes=8)).isoformat()))  # within skew tolerance
    # Stored exactly as the phone sent it; never rewritten to server time.
    assert stored.location_captured_at == datetime.fromisoformat((now + timedelta(minutes=8)).isoformat())
    PropertyDraft(**draft("2026-11-20T05:00:00+05:30"))  # offset preserved
    for bad, message in (
        ((now + timedelta(minutes=30)).isoformat(), "clock is ahead"),
        ("2019-12-31T23:00:00+00:00", "not valid"),
        ("2026-11-20T05:00:00", "time zone"),
    ):
        with pytest.raises(ValidationError, match=message):
            PropertyDraft(**draft(bad))


def test_no_customer_reachable_endpoint_exposes_exact_coordinates(database, monkeypatch):
    """GEO-M1.1 (M1) sweep: a signed-in customer never receives an approved listing's
    exact coordinates or location metadata, from any customer-reachable route."""
    import io
    import json
    import auth
    import groq
    import main
    import nearby_places
    import property_media

    connect = database
    filters = {"bedrooms": 3, "location": "Visakhapatnam"}

    def extract(**kwargs):
        content = main.SearchRequirements(intent="search", **filters).model_dump_json()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    fake_groq = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=extract)))
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-only")
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "test-only")
    monkeypatch.setattr(groq, "Groq", lambda **kwargs: fake_groq)
    monkeypatch.setattr(main, "client", fake_groq)
    for module in (main, property_media, auth, nearby_places):
        if hasattr(module, "get_connection"):
            monkeypatch.setattr(module, "get_connection", connect)
    monkeypatch.setattr(
        nearby_places, "build_opener",
        lambda *h: SimpleNamespace(open=lambda request, timeout: io.BytesIO(
            b'{"places":[{"id":"school-1","displayName":{"text":"Sweep School"},'
            b'"formattedAddress":"Road 1, Visakhapatnam"}]}')),
    )

    agent, admin, customer = [staff(connect, role) for role in ("agent", "admin", "customer")]

    def headers(user):
        return {"Authorization": f"Bearer {create_access_token(user['id'])['access_token']}"}

    LAT, LNG = "17.987654", "83.123456"
    listing = {
        "title": "Sweep Residency", "property_type": "flat", "locality": "Madhurawada",
        "city": "Visakhapatnam", "price": 6100000, "area_sqft": 1150, "bedrooms": 3,
        "owner_name": "Owner", "owner_phone": "+91 90000 00001", "listing_consent": True,
        "latitude": float(LAT), "longitude": float(LNG), "location_source": "gps",
        "location_accuracy_m": 7.5, "location_captured_at": GPS_TIME,
    }
    with TestClient(main.app) as http:
        created = http.post("/staff/properties", json=listing, headers=headers(agent))
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        path = f"/staff/properties/{pid}"
        version = http.get(path, headers=headers(agent)).json()["version"]
        submitted = http.post(f"{path}/submit", json={"version": version}, headers=headers(agent))
        approved = http.post(f"{path}/approve", json={"version": submitted.json()["version"]}, headers=headers(admin))
        assert approved.status_code == 200, approved.text

        c = headers(customer)
        conversation = http.post("/conversations", headers=c)
        responses = {
            "GET /properties": http.get("/properties"),
            "GET /properties (filtered)": http.get("/properties", params=filters),
            "GET gallery": http.get(f"/properties/{pid}/gallery"),
            "GET panoramas": http.get(f"/properties/{pid}/panoramas"),
            "GET nearby": http.get(f"/properties/{pid}/nearby", params={"category": "schools"}, headers=c),
            "POST /conversations": conversation,
            "GET /conversations": http.get("/conversations", headers=c),
            "POST /chat (search)": http.post("/chat", json={"message": "3 bhk in Visakhapatnam",
                                                           "conversation_id": conversation.json().get("id")}, headers=c),
            "GET /auth/me": http.get("/auth/me", headers=c),
        }
        if conversation.status_code < 300:
            responses["GET /conversations/{id}"] = http.get(f"/conversations/{conversation.json()['id']}", headers=c)
        # Staff-only routes stay closed to customers.
        for staff_path in (path, "/staff/properties", "/staff/marketing/jobs"):
            assert http.get(staff_path, headers=c).status_code == 403, staff_path

    assert responses["GET nearby"].status_code == 200, responses["GET nearby"].text
    assert responses["GET nearby"].json()["places"], "Nearby Places still works"
    assert responses["GET /properties"].status_code == 200
    assert any(p["id"] == pid for p in responses["GET /properties"].json()["properties"])
    for label, response in responses.items():
        body = response.text
        for secret in (LAT, LNG, "17.98765", "83.12345", "location_source", "location_accuracy", "location_captured"):
            assert secret not in body, f"{label} leaked {secret!r}"
