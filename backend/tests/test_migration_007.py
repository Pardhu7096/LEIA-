"""Migration 007 (room panoramas) on a database shaped like development.

Every test starts from a database built and tracked by 000-006 that already holds
listings made through the API: a published listing with a single (legacy) 360
panorama, and a draft with a cover, a single panorama and gallery photos. Covered:
the read-only dry run, the expectation guard, the upgrade (only 007's schema
change, all data unchanged), multi-room panoramas next to the single panorama,
the database rules behind them, and the guarded rollback. Needs TEST_DATABASE_URL."""

import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_core_schema import BACKEND, MIGRATIONS, scratch  # noqa: F401

os.environ.setdefault("JWT_SECRET", "isolated-test-secret-never-use-in-production-12345")
import migrate_crm  # noqa: E402
import reconcile_migrations  # noqa: E402

M007 = "007_room_panoramas.sql"
DOWN = BACKEND / "rollback" / "007_room_panoramas.down.sql"
PASSWORD = "correct-horse-battery-1"


def jpeg(width, height, colour):
    buffer = BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, "JPEG")
    return buffer.getvalue()


COVER, PHOTO = jpeg(600, 400, "green"), jpeg(600, 400, "white")
LEGACY_360, ROOM_360 = jpeg(1024, 512, "blue"), jpeg(1024, 512, "orange")


def build_006(connect):
    """Tracked 000-006, exactly as the development database is before step 4."""
    with connect() as connection:
        connection.execute("""CREATE TABLE public.crm_schema_migrations (
            name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        for path in MIGRATIONS[:7]:
            connection.execute(path.read_text())
            connection.execute("INSERT INTO crm_schema_migrations (name) VALUES (%s)", (path.name,))
        # Step 3 (14 Nov) gave the API role read access to the history.
        connection.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
                GRANT SELECT ON public.crm_schema_migrations TO leia_app;
            END IF; END $$;""")


def status(path):
    assert reconcile_migrations.main(["status", "--json", "--output", str(path)]) == 0
    return path


@pytest.fixture
def site(scratch, monkeypatch):  # noqa: F811
    """A 006 database with real listings created through the API."""
    build_006(scratch)
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    import main

    http = TestClient(main.app, raise_server_exceptions=False)
    http.__enter__()

    def account(name, role):
        email = f"{name.lower()}@example.com"
        assert http.post("/auth/register", json={
            "name": name, "email": email, "password": PASSWORD}).status_code == 201
        with scratch() as connection:
            connection.execute("UPDATE users SET role = %s WHERE email = %s", (role, email))
        response = http.post("/auth/staff/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    agent, admin = account("Ravi", "agent"), account("Meena", "admin")

    def ok(response, code=200):
        assert response.status_code == code, response.text
        return response.json()

    def listing(title):
        payload = {"title": title, "property_type": "flat", "locality": "Madhurawada",
                   "city": "Visakhapatnam", "price": 7500000, "area_sqft": 1130,
                   "bedrooms": 3, "listing_consent": True}
        created = ok(http.post("/staff/properties", json=payload, headers=agent), 201)
        path, version = f"/staff/properties/{created['id']}", created["version"]
        version = ok(http.put(f"{path}/media/cover", params={"version": version},
                              content=COVER, headers=agent))["version"]
        version = ok(http.put(f"{path}/media/panorama", params={"version": version},
                              content=LEGACY_360, headers=agent))["version"]
        photos = []
        for room in ("Kitchen", "Hall"):
            added = ok(http.post(f"{path}/gallery", params={"version": version, "room": room},
                                 content=PHOTO, headers=agent), 201)
            version, photos = added["version"], photos + [added["id"]]
        version = ok(http.put(f"{path}/gallery/{photos[0]}/cover", params={"version": version},
                              headers=agent))["version"]
        return {"id": created["id"], "path": path, "version": version}

    published = listing("Published before 007")
    submitted = ok(http.post(f"{published['path']}/submit", json={"version": published["version"]},
                             headers=agent))
    ok(http.post(f"{published['path']}/approve", json={"version": submitted["version"]},
                 headers=admin))
    draft = listing("Draft before 007")
    try:
        yield {"http": http, "agent": agent, "admin": admin, "ok": ok,
               "published": published, "draft": draft}
    finally:
        http.__exit__(None, None, None)


def run_down(connect):
    with connect(autocommit=True) as connection:
        connection.execute(DOWN.read_text())


def history(connect):
    with connect() as connection:
        return [r["name"] for r in connection.execute(
            "SELECT name FROM crm_schema_migrations ORDER BY name")]


# ---- dry run and guard ---------------------------------------------------------------

def test_dry_run_is_read_only_and_shows_exactly_007(site, scratch, monkeypatch, capsys, tmp_path):  # noqa: F811
    before = status(tmp_path / "before.json")
    monkeypatch.setenv("PGOPTIONS", "-c default_transaction_read_only=on")
    capsys.readouterr()
    assert migrate_crm.migrate(dry_run=True, expect=[M007]) == 0
    out = capsys.readouterr().out
    sha = migrate_crm.file_sha256(BACKEND / "migrations" / M007)
    assert f"  apply {M007}  SHA-256 {sha}" in out
    for line in (BACKEND / "migrations" / M007).read_text().strip().splitlines():
        assert f"    | {line}" in out
    assert "apply 00" not in out.replace(f"apply {M007}", "")  # nothing else
    assert out.strip().endswith("Dry run: nothing changed.")
    monkeypatch.delenv("PGOPTIONS")
    after = status(tmp_path / "after.json")
    assert reconcile_migrations.main(["compare-status", str(before), str(after)]) == 0
    assert M007 not in history(scratch)


def test_expect_guard_refuses_other_pending_sets(site, scratch, capsys):  # noqa: F811
    assert migrate_crm.migrate(expect=["007_other.sql"]) == 2
    assert migrate_crm.migrate(dry_run=True, expect=[]) == 2
    assert "refusing. Nothing changed." in capsys.readouterr().out
    assert M007 not in history(scratch)


# ---- upgrade ---------------------------------------------------------------------------

def test_upgrade_changes_only_the_schema_and_keeps_every_row(site, scratch, tmp_path, capsys):  # noqa: F811
    before = status(tmp_path / "before.json")
    capsys.readouterr()
    assert migrate_crm.migrate(expect=[M007]) == 0
    assert f"Applied {M007}" in capsys.readouterr().out.splitlines()
    after = status(tmp_path / "after.json")
    capsys.readouterr()
    assert reconcile_migrations.main(
        ["verify-migration", str(before), str(after), "--applied", M007]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and out.strip().endswith("VERIFIED.")
    assert "schema changes are exactly those of 007_room_panoramas.sql (2)" in out
    assert history(scratch) == [p.name for p in MIGRATIONS]


def test_verify_migration_catches_extra_changes(site, scratch, tmp_path, capsys):  # noqa: F811
    before = status(tmp_path / "before.json")
    assert migrate_crm.migrate(expect=[M007]) == 0
    with scratch() as connection:
        connection.execute("UPDATE property_media SET room = 'Lounge' WHERE room = 'Hall'")
        connection.execute("CREATE INDEX stray ON property_media (uploaded_at)")
    after = status(tmp_path / "after.json")
    capsys.readouterr()
    assert reconcile_migrations.main(
        ["verify-migration", str(before), str(after), "--applied", M007]) == 1
    out = capsys.readouterr().out
    assert "FAIL  content checksums unchanged" in out
    assert "FAIL  schema changes are exactly those of" in out
    assert "FAIL  after: schema is exactly the state after" in out


def test_room_panoramas_fail_safely_before_007(site, scratch):  # noqa: F811
    """With the old schema (also after a rollback) the room feature is refused by the
    database and nothing is half-written; everything else keeps working."""
    http, draft = site["http"], site["draft"]
    response = http.put(f"{draft['path']}/panoramas/Hall", params={"version": draft["version"]},
                        content=ROOM_360, headers=site["agent"])
    assert response.status_code == 500
    with scratch() as connection:
        assert connection.execute(
            "SELECT count(*) AS n FROM property_media WHERE kind = 'panorama_room'").fetchone()["n"] == 0
        assert connection.execute("SELECT version FROM properties WHERE id = %s",
                                  (draft["id"],)).fetchone()["version"] == draft["version"]
    assert http.get(f"/properties/{site['published']['id']}/gallery").status_code == 200


# ---- multi-room panoramas and the single panorama ---------------------------------------

def test_multi_room_panoramas_live_beside_the_single_panorama(site, scratch):  # noqa: F811
    assert migrate_crm.migrate(expect=[M007]) == 0
    http, ok, agent, admin = site["http"], site["ok"], site["agent"], site["admin"]
    published, draft = site["published"], site["draft"]

    # The listing published before 007 is unchanged: still public, single tour served.
    cards = {c["id"]: c for c in ok(http.get("/properties"))["properties"]}
    legacy_tour = cards[published["id"]]["tour"]
    assert legacy_tour and http.get(legacy_tour).status_code == 200
    assert ok(http.get(f"/properties/{published['id']}/panoramas")) == []
    assert len(ok(http.get(f"/properties/{published['id']}/gallery"))) == 2

    # Several rooms on the draft, next to its single panorama.
    path, version = draft["path"], draft["version"]
    single_before = http.get(f"{path}/media/panorama", headers=agent).content
    for room in ("Bedroom 1", "Bedroom 2", "Living Room"):
        version = ok(http.put(f"{path}/panoramas/{room}", params={"version": version},
                              content=ROOM_360, headers=agent))["version"]
    rooms = ok(http.get(f"{path}/panoramas", headers=agent))
    assert [r["room"] for r in rooms] == ["Bedroom 1", "Bedroom 2", "Living Room"]

    # Same room in other letter case and spacing replaces it instead of adding one.
    replaced = ok(http.put(f"{path}/panoramas/  bedroom   1 ", params={"version": version},
                           content=jpeg(2048, 1024, "red"), headers=agent))
    version = replaced["version"]
    rooms = ok(http.get(f"{path}/panoramas", headers=agent))
    assert len(rooms) == 3 and replaced["id"] in {r["id"] for r in rooms}
    assert {r["id"]: r["room"] for r in rooms}[replaced["id"]] == "bedroom 1"

    # Room panoramas are not gallery photos, and the single panorama is untouched.
    assert len(ok(http.get(f"{path}/gallery", headers=agent))) == 2
    assert http.get(f"{path}/media/panorama", headers=agent).content == single_before

    version = ok(http.delete(f"{path}/panoramas/Living%20Room", params={"version": version},
                             headers=agent))["version"]
    assert http.delete(f"{path}/panoramas/Living%20Room", params={"version": version},
                       headers=agent).status_code == 404
    assert http.get(f"{path}/media/panorama", headers=agent).content == single_before

    # Publish: customers see the single tour and every room tour.
    submitted = ok(http.post(f"{path}/submit", json={"version": version}, headers=agent))
    ok(http.post(f"{path}/approve", json={"version": submitted["version"]}, headers=admin))
    cards = {c["id"]: c for c in ok(http.get("/properties"))["properties"]}
    assert http.get(cards[draft["id"]]["tour"]).status_code == 200
    public_rooms = ok(http.get(f"/properties/{draft['id']}/panoramas"))
    # The replaced room keeps the spelling of its latest upload (existing API behaviour).
    assert sorted(r["room"] for r in public_rooms) == ["Bedroom 2", "bedroom 1"]
    for room in public_rooms:
        assert http.get(room["tour"]).status_code == 200
    assert len(ok(http.get(f"/properties/{draft['id']}/gallery"))) == 2


def test_database_rules_after_007(site, scratch):  # noqa: F811
    assert migrate_crm.migrate(expect=[M007]) == 0
    property_id = site["draft"]["id"]

    def insert(kind, room=None, cover=False):
        with scratch() as connection:
            connection.execute(
                "INSERT INTO property_media (id, property_id, kind, room, is_gallery_cover, content)"
                " VALUES (%s, %s, %s, %s, %s, '\\x01')", (uuid4(), property_id, kind, room, cover))

    insert("panorama_room", "Study")
    for kind, room, cover, error in [
        ("panorama_room", None, False, psycopg.errors.CheckViolation),        # room required
        ("panorama_room", "   ", False, psycopg.errors.CheckViolation),       # not blank
        ("panorama_room", "x" * 61, False, psycopg.errors.CheckViolation),    # 60 characters
        ("panorama_room", "Terrace", True, psycopg.errors.CheckViolation),    # never a cover
        ("panorama_room", "STUDY", False, psycopg.errors.UniqueViolation),    # one per room
        ("panorama", None, False, psycopg.errors.UniqueViolation),            # single slot kept
        ("panorama", "Hall", False, psycopg.errors.CheckViolation),           # single has no room
        ("video", "Hall", False, psycopg.errors.CheckViolation),              # unknown kind
    ]:
        with pytest.raises(error):
            insert(kind, room, cover)
    # Another listing may use the same room name.
    with scratch() as connection:
        other = site["published"]["id"]
        connection.execute(
            "INSERT INTO property_media (id, property_id, kind, room, content)"
            " VALUES (%s, %s, 'panorama_room', 'Study', '\\x01')", (uuid4(), other))
        assert connection.execute(
            "SELECT count(*) AS n FROM property_media WHERE kind = 'panorama_room'").fetchone()["n"] == 2


# ---- rollback ----------------------------------------------------------------------------

def test_rollback_restores_the_006_state_exactly_and_007_reapplies(site, scratch, tmp_path, capsys):  # noqa: F811
    before = status(tmp_path / "before.json")
    assert migrate_crm.migrate(expect=[M007]) == 0
    run_down(scratch)
    rolled_back = status(tmp_path / "rolled_back.json")
    capsys.readouterr()
    assert reconcile_migrations.main(["compare-status", str(before), str(rolled_back)]) == 0
    assert capsys.readouterr().out.strip() == "Identical."
    assert migrate_crm.migrate(expect=[M007]) == 0  # can be applied again
    assert history(scratch) == [p.name for p in MIGRATIONS]


def test_rollback_refuses_while_room_panoramas_exist(site, scratch, tmp_path):  # noqa: F811
    assert migrate_crm.migrate(expect=[M007]) == 0
    draft = site["draft"]
    site["ok"](site["http"].put(f"{draft['path']}/panoramas/Hall", params={"version": draft["version"]},
                                content=ROOM_360, headers=site["agent"]))
    before = status(tmp_path / "before.json")
    with pytest.raises(psycopg.errors.RaiseException, match="1 room panorama"):
        run_down(scratch)
    after = status(tmp_path / "after.json")
    assert reconcile_migrations.main(["compare-status", str(before), str(after)]) == 0


def test_rollback_refuses_when_007_is_not_recorded(site, scratch):  # noqa: F811
    with pytest.raises(psycopg.errors.RaiseException, match="not recorded"):
        run_down(scratch)
    assert M007 not in history(scratch)


def test_rollback_script_is_outside_the_migration_chain():
    assert DOWN.exists() and DOWN.parent != BACKEND / "migrations"
    assert not list((BACKEND / "migrations").glob("*down*"))
    assert Path(migrate_crm.MIGRATIONS) == BACKEND / "migrations"
