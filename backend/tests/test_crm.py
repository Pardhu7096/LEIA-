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
        connection.execute("""
            CREATE TABLE auth_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                refresh_token_hash TEXT NOT NULL UNIQUE,
                expires_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ
            )
        """)
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

    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    monkeypatch.setattr(groq, "Groq", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=extract))
    ))
    import main
    import property_media
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

        cards = results()
        assert cards == [{
            "id": listing["id"],
            "title": payload["title"],
            "property_type": "flat",
            "location": "Madhurawada, Visakhapatnam",
            "price": 7500000,
            "area_sqft": 1130,
            "bedrooms": 3,
            "image": f"/property-media/{kitchen}",
            "tour": public_tour,
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


def test_nearby_search_uses_property_pin_and_returns_property_origin(monkeypatch):
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
    assert parse_qs(url.query)["origin"] == ["17.7,83.3"]
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
            {"filters": filters}, {"properties": properties}, {"id": 1}, {"id": 2},
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
            {"title": "SS Castle", "latitude": 17.7, "longitude": 83.3}, {"id": 3}, {"id": 4},
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
    assert queries[4][1][1] == "Ask again to get a fresh walking estimate from Google Maps."
    assert len(calls) == 2


def test_selected_property_facts_and_walk_use_only_that_listing(monkeypatch):
    import rate_limits
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
                {"filters": filters}, selected_listing, {"id": 1}, {"id": 2},
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
        assert "Bathrooms: Not listed" in ask("How many bathrooms does it have?")
        assert "Car parking spaces: 0" in ask("Does it have parking?")
        assert "I don’t have that detail for SS Castle" in ask("Does it have a balcony?")
        assert "no longer available" in ask("How big is it?", None)
        assert "450 m" in ask("How far is the nearest hospital?")
        walk.assert_called_once_with(17.7, 83.3, "hospitals")
        assert cursor.execute.call_args_list[3].args[1][1] == (
            "Ask again to get a fresh walking estimate from Google Maps."
        )
        for index in (2, 3):
            assert cursor.execute.call_args_list[index].args[1][-1] == property_id

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
