"""Digital Studio M1: marketing jobs created from approved CRM listings.

Unit tests run anywhere. Integration tests need TEST_DATABASE_URL pointing at an
empty, local, disposable database named leia_marketing_test or crm_test
(conftest.py blocks every other connection).
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth  # noqa: E402
import crm  # noqa: E402
import marketing_jobs  # noqa: E402
import migrate_crm  # noqa: E402
import studio_inputs  # noqa: E402
from auth import create_access_token, require_admin, require_staff  # noqa: E402
from marketing_models import ALLOWED_TRANSITIONS, can_transition  # noqa: E402

ALLOWED_DATABASES = {"leia_marketing_test", "crm_test"}


# ------------------------------------------------------------------ unit tests


def test_private_fields_never_reach_the_snapshot():
    # Everything the CRM keeps private stays out of snapshots, and so does the
    # GEO-M1 coordinate provenance (internal review data).
    location_meta = {"location_source", "location_accuracy_m", "location_captured_at"}
    # Migration 014: RERA verification bookkeeping and free-text project notes stay internal.
    project_internal = {"rera_source", "rera_checked_on", "project_notes"}
    assert marketing_jobs.PRIVATE_FIELDS == set(crm.PRIVATE_FIELDS) | location_meta | project_internal
    assert not marketing_jobs.PRIVATE_FIELDS & set(marketing_jobs.SNAPSHOT_FIELDS)
    for name in ("title", "price", "bedrooms", "area_sqft", "facing", "locality"):
        assert name in marketing_jobs.SNAPSHOT_FIELDS


def test_transition_table():
    statuses = set(ALLOWED_TRANSITIONS)
    assert all(targets <= statuses for targets in ALLOWED_TRANSITIONS.values())
    assert can_transition("failed", "queued")
    assert can_transition("queued", "cancelled")
    assert not can_transition("queued", "published")
    assert not can_transition("queued", "queued")
    assert not can_transition("cancelled", "queued")
    assert not can_transition("published", "cancelled")
    assert not can_transition("unknown", "queued")
    # Publishing always passes through human approval.
    assert {s for s, targets in ALLOWED_TRANSITIONS.items() if "publishing" in targets} == {
        "approved"
    }


def test_clean_error():
    assert marketing_jobs.clean_error(None) is None
    assert marketing_jobs.clean_error("  \n ") is None
    assert marketing_jobs.clean_error("ffmpeg\x00 failed\n\tline 2") == "ffmpeg failed line 2"
    assert len(marketing_jobs.clean_error("x" * 2000)) == 500


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("true", True), ("1", True), ("false", False), (" OFF ", False), ("0", False)],
)
def test_kill_switch_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("MARKETING_JOBS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MARKETING_JOBS_ENABLED", value)
    assert marketing_jobs.enabled() is expected


def test_route_permissions():
    expected = {
        ("GET", "/staff/marketing/jobs"): require_staff,
        ("GET", "/staff/marketing/jobs/{job_id}"): require_staff,
        ("POST", "/staff/marketing/jobs/{job_id}/retry"): require_admin,
        ("POST", "/staff/marketing/jobs/{job_id}/cancel"): require_admin,
        ("POST", "/staff/marketing/reconcile"): require_admin,
    }
    found = {}
    for route in marketing_jobs.router.routes:
        for method in route.methods:
            found[(method, route.path)] = {d.call for d in route.dependant.dependencies}
    assert set(found) == set(expected)
    for key, dependency in expected.items():
        assert dependency in found[key], key


def test_enqueue_on_publish_swallows_failures(monkeypatch):
    monkeypatch.delenv("MARKETING_JOBS_ENABLED", raising=False)

    def broken():
        raise psycopg.OperationalError("savepoint failed")

    connection = SimpleNamespace(transaction=broken)
    assert marketing_jobs.enqueue_on_publish(connection, uuid4(), uuid4()) is None


def test_disabled_switch_does_not_touch_the_connection(monkeypatch):
    monkeypatch.setenv("MARKETING_JOBS_ENABLED", "false")
    assert marketing_jobs.enqueue_on_publish(object(), uuid4(), uuid4()) is None


# ----------------------------------------------------------- integration setup


@pytest.fixture(scope="module")
def database():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated disposable database.")

    def connect():
        return psycopg.connect(url, row_factory=dict_row)

    with connect() as connection:
        name = connection.execute("SELECT current_database() AS name").fetchone()["name"]
        if name not in ALLOWED_DATABASES:
            pytest.fail("Use an explicitly allowed disposable test database.")
        if connection.execute("SELECT to_regclass('public.users') AS t").fetchone()["t"]:
            pytest.fail("TEST_DATABASE_URL must point to an empty disposable database.")
        connection.execute("""CREATE TABLE public.users (
            id UUID PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
            role TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")

    patched = (auth, crm, marketing_jobs, migrate_crm, studio_inputs)
    originals = [module.get_connection for module in patched]
    try:
        for module in patched:
            module.get_connection = connect
        migrate_crm.migrate()
        migrate_crm.migrate()  # tracked rerun must be harmless
        yield connect
    finally:
        for module, original in zip(patched, originals):
            module.get_connection = original


@pytest.fixture(scope="module")
def http(database):
    app = FastAPI()
    app.include_router(crm.router)
    app.include_router(marketing_jobs.router)
    app.include_router(studio_inputs.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def default_switch(monkeypatch):
    monkeypatch.delenv("MARKETING_JOBS_ENABLED", raising=False)


def make_user(connect, role):
    user = {"id": uuid4(), "name": f"Test {role}", "email": f"{uuid4()}@example.com", "role": role}
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, name, email, role) VALUES (%s, %s, %s, %s)",
            (user["id"], user["name"], user["email"], user["role"]),
        )
    return user


def headers(user):
    return {"Authorization": f"Bearer {create_access_token(user['id'])['access_token']}"}


LISTING = {
    "title": "Test Studio Residency",
    "property_type": "flat",
    "facing": "east",
    "locality": "Madhurawada",
    "city": "Visakhapatnam",
    "price": 6500000,
    "area_sqft": 1180,
    "bedrooms": 2,
    "owner_name": "PRIVATE-OWNER-NAME",
    "owner_phone": "PRIVATE-OWNER-PHONE",
    "agent_notes": "PRIVATE-AGENT-NOTES",
    "listing_consent": True,
}


def submitted_listing(http, owner):
    created = http.post("/staff/properties", json=LISTING, headers=headers(owner))
    assert created.status_code == 201, created.text
    listing = created.json()
    submitted = http.post(
        f"/staff/properties/{listing['id']}/submit",
        json={"version": listing["version"]},
        headers=headers(owner),
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()


def approve(http, admin, listing):
    return http.post(
        f"/staff/properties/{listing['id']}/approve",
        json={"version": listing["version"]},
        headers=headers(admin),
    )


def jobs_for(connect, property_id):
    with connect() as connection:
        return connection.execute(
            "SELECT * FROM marketing_jobs WHERE property_id = %s", (property_id,)
        ).fetchall()


# ----------------------------------------------------------- integration tests


def test_approval_creates_exactly_one_job_and_event(database, http):
    owner, admin = make_user(database, "agent"), make_user(database, "admin")
    listing = submitted_listing(http, owner)
    assert jobs_for(database, listing["id"]) == []  # submit alone creates nothing

    approved = approve(http, admin, listing)
    assert approved.status_code == 200, approved.text
    version = approved.json()["version"]

    [job] = jobs_for(database, listing["id"])
    assert job["status"] == "queued"
    assert job["job_type"] == "reel_v1"
    assert job["property_version"] == version
    assert job["created_by"] == admin["id"]
    snapshot = job["property_snapshot"]
    assert snapshot["property_version"] == version
    assert snapshot["property"]["title"] == LISTING["title"]
    assert snapshot["property"]["price"] == LISTING["price"]
    assert snapshot["property"]["area_sqft"] == LISTING["area_sqft"]
    assert snapshot["property"]["facing"] == "east"
    assert snapshot["agent"] == {"id": str(owner["id"]), "name": owner["name"]}
    assert "PRIVATE-" not in json.dumps(snapshot)

    with database() as connection:
        [event] = connection.execute(
            "SELECT * FROM outbox_events WHERE aggregate_id = %s", (listing["id"],)
        ).fetchall()
        history = connection.execute(
            "SELECT from_status, to_status FROM marketing_job_transitions WHERE job_id = %s",
            (job["id"],),
        ).fetchall()
    assert event["event_type"] == "property.published"
    assert event["aggregate_version"] == version
    assert event["dispatched_at"] is None
    assert job["source_event_id"] == event["id"]
    assert history == [{"from_status": None, "to_status": "queued"}]

    # A repeated approval is rejected and adds nothing.
    again = approve(http, admin, {**listing, "version": version})
    assert again.status_code == 409
    assert len(jobs_for(database, listing["id"])) == 1
    # The reconciler sees nothing missing.
    assert http.post("/staff/marketing/reconcile", headers=headers(admin)).json() == {"created": 0}


def test_concurrent_approvals_create_one_job(database, http):
    owner, admin = make_user(database, "agent"), make_user(database, "admin")
    listing = submitted_listing(http, owner)

    def attempt(_):
        try:
            crm.transition_property(listing["id"], listing["version"], admin, publish=True)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = sorted(pool.map(attempt, range(4)))
    assert results == [200, 409, 409, 409]
    assert len(jobs_for(database, listing["id"])) == 1


def test_marketing_failure_never_blocks_publication(database, http):
    owner, admin = make_user(database, "agent"), make_user(database, "admin")
    listing = submitted_listing(http, owner)
    with database() as connection:
        connection.execute("ALTER TABLE marketing_jobs RENAME TO marketing_jobs_offline")
    try:
        approved = approve(http, admin, listing)
    finally:
        with database() as connection:
            connection.execute("ALTER TABLE marketing_jobs_offline RENAME TO marketing_jobs")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    with database() as connection:
        stored = connection.execute(
            "SELECT status FROM properties WHERE id = %s", (listing["id"],)
        ).fetchone()
        events = connection.execute(
            "SELECT count(*) AS n FROM outbox_events WHERE aggregate_id = %s", (listing["id"],)
        ).fetchone()["n"]
    assert stored["status"] == "approved"
    assert events == 0  # the whole savepoint rolled back, not half of it
    assert jobs_for(database, listing["id"]) == []

    first = http.post("/staff/marketing/reconcile", headers=headers(admin))
    assert first.status_code == 200, first.text
    assert first.json()["created"] >= 1
    [job] = jobs_for(database, listing["id"])
    assert job["status"] == "queued"
    assert http.post("/staff/marketing/reconcile", headers=headers(admin)).json() == {"created": 0}


def test_kill_switch_defers_to_reconcile(database, http, monkeypatch):
    owner, admin = make_user(database, "agent"), make_user(database, "admin")
    listing = submitted_listing(http, owner)
    monkeypatch.setenv("MARKETING_JOBS_ENABLED", "false")
    assert approve(http, admin, listing).status_code == 200
    assert jobs_for(database, listing["id"]) == []
    with database() as connection:
        assert marketing_jobs.reconcile(connection) >= 1
    assert len(jobs_for(database, listing["id"])) == 1


def test_permissions_and_agent_scope(database, http):
    owner, other, admin = (make_user(database, r) for r in ("agent", "agent", "admin"))
    customer = make_user(database, "customer")
    listing = submitted_listing(http, owner)
    assert approve(http, admin, listing).status_code == 200
    [job] = jobs_for(database, listing["id"])
    job_path = f"/staff/marketing/jobs/{job['id']}"

    assert http.get("/staff/marketing/jobs").status_code == 401
    assert http.get("/staff/marketing/jobs", headers=headers(customer)).status_code == 403
    assert http.get(job_path, headers=headers(customer)).status_code == 403

    mine = http.get("/staff/marketing/jobs", headers=headers(owner))
    assert mine.status_code == 200
    assert [row["id"] for row in mine.json()] == [str(job["id"])]
    assert mine.json()[0]["property_title"] == LISTING["title"]
    assert http.get(job_path, headers=headers(owner)).status_code == 200

    assert http.get("/staff/marketing/jobs", headers=headers(other)).json() == []
    assert http.get(job_path, headers=headers(other)).status_code == 404
    assert http.get(f"/staff/marketing/jobs/{uuid4()}", headers=headers(admin)).status_code == 404

    body = {"version": job["version"]}
    for action in ("retry", "cancel"):
        assert http.post(f"{job_path}/{action}", json=body, headers=headers(owner)).status_code == 403
    assert http.post("/staff/marketing/reconcile", headers=headers(owner)).status_code == 403
    assert http.get(job_path, headers=headers(admin)).json()["status"] == "queued"


def test_failure_retry_and_cancel(database, http):
    owner, admin = make_user(database, "agent"), make_user(database, "admin")
    listing = submitted_listing(http, owner)
    assert approve(http, admin, listing).status_code == 200
    [job] = jobs_for(database, listing["id"])
    path = f"/staff/marketing/jobs/{job['id']}"

    # Only failed jobs can be retried.
    queued_retry = http.post(f"{path}/retry", json={"version": job["version"]}, headers=headers(admin))
    assert queued_retry.status_code == 409

    # Simulate the future render worker reporting a failure.
    with database() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE marketing_jobs SET status = 'processing', attempts = 1, "
            "version = version + 1 WHERE id = %s", (job["id"],),
        )
        marketing_jobs.mark_failed(cursor, job["id"], "ffmpeg exited 1\n" + "x" * 900)
    failed = http.get(path, headers=headers(admin)).json()
    assert failed["status"] == "failed"
    assert failed["last_error"].startswith("ffmpeg exited 1 x")
    assert len(failed["last_error"]) == 500

    stale = http.post(f"{path}/retry", json={"version": job["version"]}, headers=headers(admin))
    assert stale.status_code == 409
    retried = http.post(f"{path}/retry", json={"version": failed["version"]}, headers=headers(admin))
    assert retried.status_code == 200, retried.text
    retried = retried.json()
    assert retried["status"] == "queued"
    assert retried["attempts"] == 0
    assert retried["last_error"] is None
    assert retried["transitions"][-1]["reason"].startswith("Retry after: ffmpeg exited 1")

    cancelled = http.post(f"{path}/cancel", json={"version": retried["version"]}, headers=headers(admin))
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    again = http.post(
        f"{path}/cancel", json={"version": cancelled.json()["version"]}, headers=headers(admin)
    )
    assert again.status_code == 409
    assert [t["to_status"] for t in cancelled.json()["transitions"]] == [
        "queued", "failed", "queued", "cancelled",
    ]
    assert http.post(f"{path}/cancel", json={"version": 0}, headers=headers(admin)).status_code == 422


def test_list_filters_and_pagination(database, http):
    owner, admin = make_user(database, "agent"), make_user(database, "admin")
    ids = []
    for _ in range(3):
        listing = submitted_listing(http, owner)
        assert approve(http, admin, listing).status_code == 200
        ids.append(listing["id"])

    mine = http.get("/staff/marketing/jobs", params={"limit": 2}, headers=headers(owner)).json()
    assert len(mine) == 2
    rest = http.get(
        "/staff/marketing/jobs",
        params={"limit": 2, "before": mine[-1]["id"]},
        headers=headers(owner),
    ).json()
    assert len(rest) == 1
    assert {row["property_id"] for row in mine + rest} == set(ids)

    one = http.get(
        "/staff/marketing/jobs", params={"property_id": ids[0]}, headers=headers(admin)
    ).json()
    assert [row["property_id"] for row in one] == [ids[0]]
    none = http.get(
        "/staff/marketing/jobs",
        params={"status": "published", "property_id": ids[0]},
        headers=headers(admin),
    ).json()
    assert none == []
    bad = http.get("/staff/marketing/jobs", params={"status": "bogus"}, headers=headers(admin))
    assert bad.status_code == 422


def test_approval_captures_walkthrough_and_admin_verified_contact(database, http, monkeypatch):
    import boto3

    uploads = []

    def put_object(**kwargs):
        uploads.append((kwargs["Key"], kwargs["Body"].read()))

    monkeypatch.setenv("STUDIO_S3_BUCKET", "private-test-bucket")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: SimpleNamespace(put_object=put_object))
    monkeypatch.setattr(studio_inputs, "validate_video", lambda path: None)
    agent, other, admin = (make_user(database, role) for role in ("agent", "agent", "admin"))
    contact_url = f"/staff/marketing/agents/{agent['id']}/verified-contact"
    phone = {"phone": "+919876543210"}
    assert http.put(contact_url, headers=headers(agent), json=phone).status_code == 403
    assert http.put(contact_url, headers=headers(admin), json=phone).status_code == 204

    created = http.post("/staff/properties", json=LISTING, headers=headers(agent))
    assert created.status_code == 201, created.text
    draft = created.json()
    # Digital Studio reads the owning agent from the draft details (summaries omit it).
    owner = http.get(f"/staff/properties/{draft['id']}", headers=headers(admin))
    assert owner.status_code == 200 and str(owner.json()["created_by"]) == str(agent["id"])
    upload_url = f"/staff/marketing/properties/{draft['id']}/walkthrough"
    video_headers = {"Content-Type": "video/mp4"}
    params = {"version": draft["version"]}
    assert http.put(upload_url, params=params, headers={**headers(other), **video_headers},
                    content=b"test-video").status_code == 404
    uploaded = http.put(upload_url, params=params, headers={**headers(agent), **video_headers},
                        content=b"test-video")
    assert uploaded.status_code == 200, uploaded.text
    media = uploaded.json()
    assert http.put(upload_url, params=params, headers={**headers(agent), **video_headers},
                    content=b"test-video").status_code == 409
    assert uploads == [(f"walkthroughs/{media['id']}.mp4", b"test-video")]

    submitted = http.post(
        f"/staff/properties/{draft['id']}/submit",
        json={"version": media["version"]},
        headers=headers(agent),
    )
    assert submitted.status_code == 200, submitted.text
    approved = approve(http, admin, submitted.json())
    assert approved.status_code == 200, approved.text
    [job] = jobs_for(database, draft["id"])
    snapshot = job["property_snapshot"]
    assert snapshot["agent"]["phone"] == phone["phone"]
    assert snapshot["agent"]["phone_verified_at"]
    assert snapshot["media"] == [{"id": media["id"], "kind": "walkthrough"}]


def test_studio_status_is_admin_only_and_reveals_no_settings(database, http, monkeypatch):
    monkeypatch.setenv("STUDIO_S3_BUCKET", "private-test-bucket")
    agent, admin = (make_user(database, role) for role in ("agent", "admin"))
    assert http.get("/staff/marketing/studio/status").status_code == 401
    assert http.get("/staff/marketing/studio/status", headers=headers(agent)).status_code == 403
    status = http.get("/staff/marketing/studio/status", headers=headers(admin))
    assert status.status_code == 200
    body = status.json()
    assert set(body) == {"ffprobe", "storage_configured"} and body["storage_configured"] is True
    assert "private-test-bucket" not in status.text
    monkeypatch.delenv("STUDIO_S3_BUCKET")
    assert http.get("/staff/marketing/studio/status", headers=headers(admin)).json()["storage_configured"] is False
