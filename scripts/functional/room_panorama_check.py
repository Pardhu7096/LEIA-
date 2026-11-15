"""Functional check of room-by-room 360 panoramas against a running LEIA backend.

    python room_panorama_check.py check-existing --out DIR
        Public GET requests only (no login, no writes): every published listing's single
        tour, room tours and gallery, saved as a baseline report.

    python room_panorama_check.py create --out DIR            # prints the plan only
    python room_panorama_check.py create --out DIR --apply    # WRITES (needs approval)
        Creates ONE listing titled "[QA] Room panoramas <time>" with an agent account,
        uploads labelled test images (cover, single panorama, two gallery photos, three
        room panoramas), replaces one room using other letter case, removes one room,
        submits it, approves it with an admin account, and checks what staff and
        customers get. Prints the guarded SQL that removes the listing again.

    python room_panorama_check.py compare BEFORE AFTER [--ignore LISTING_ID]
        Existing listings unchanged.

Only a backend on this computer (http://127.0.0.1 or http://localhost) is accepted.
Passwords are asked for privately and never written anywhere.
"""

import argparse
import getpass
import io
import ipaddress
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

# Distinct colours: each served image is decoded and matched to what was uploaded.
COLOURS = {
    "cover": (40, 140, 60), "single": (30, 60, 200), "Kitchen": (230, 230, 230),
    "Hall": (200, 200, 120), "Bedroom 1": (220, 120, 30), "Bedroom 2": (150, 40, 160),
    "Living Room": (30, 170, 170), "bedroom 1 (replacement)": (200, 30, 40),
}


def labelled_image(label, width, height):
    """A solid-colour JPEG with large readable text, so each tour is easy to tell apart."""
    colour = COLOURS[label]
    small = Image.new("RGB", (width // 8, height // 8), colour)
    draw = ImageDraw.Draw(small)
    text = f"LEIA QA - {label.upper()}"
    for x in range(4, small.width, max(1, small.width // 3)):
        draw.text((x, small.height // 2 - 4), text, fill=(255, 255, 255))
    image = small.resize((width, height), Image.NEAREST)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def dominant_colour(data):
    image = Image.open(io.BytesIO(data)).convert("RGB").resize((32, 16))
    counts = {}
    for pixel in getattr(image, "get_flattened_data", image.getdata)():
        key = tuple(round(c / 20) * 20 for c in pixel)
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get)


def same_colour(data, label):
    found, want = dominant_colour(data), COLOURS[label]
    return all(abs(a - b) <= 30 for a, b in zip(found, want))


class Api:
    def __init__(self, base):
        parsed = urllib.parse.urlparse(base)
        host = parsed.hostname or ""
        try:
            local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if parsed.scheme != "http" or not local or parsed.path not in ("", "/"):
            raise SystemExit(f"Refusing API {base!r}: only a backend on this computer "
                             "(http://127.0.0.1:<port> or http://localhost:<port>) is allowed.")
        self.base = base.rstrip("/")
        self.token = None

    def call(self, method, path, body=None, raw=None, query=None, expect=(200,)):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if raw is not None:
            data, headers["Content-Type"] = raw, "image/jpeg"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, content, kind = response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            status, content, kind = error.code, error.read(), error.headers.get("Content-Type", "")
        if status not in expect:
            raise AssertionError(f"{method} {path} -> {status}: {content[:300]!r}")
        if "json" in kind:
            return status, json.loads(content or b"null")
        return status, content


class Report:
    def __init__(self):
        self.results = []

    def check(self, label, ok, detail=""):
        self.results.append({"check": label, "ok": bool(ok), "detail": str(detail)})
        print(f"{'PASS' if ok else 'FAIL'}  {label}{'' if ok or not detail else ': ' + str(detail)}")
        return ok

    @property
    def failed(self):
        return [r for r in self.results if not r["ok"]]


def public_state(api):
    """Everything a customer can see about every published listing."""
    _, data = api.call("GET", "/properties")
    listings = {}
    for card in data["properties"]:
        entry = {"title": card.get("title"), "tour": card.get("tour"), "image": card.get("image")}
        if card.get("tour"):
            status, page = api.call("GET", card["tour"], expect=(200, 404))
            entry["tour_status"] = status
            entry["tour_is_page"] = status == 200 and b"pannellum" in page.lower()
            media_id = card["tour"].split("/")[2]
            status, image = api.call("GET", f"/property-media/{media_id}", expect=(200, 404))
            entry["tour_image_status"] = status
            entry["tour_image_colour"] = list(dominant_colour(image)) if status == 200 else None
        _, rooms = api.call("GET", f"/properties/{card['id']}/panoramas")
        entry["rooms"] = [{"room": r["room"], "id": r["id"], "tour": r["tour"]} for r in rooms]
        _, gallery = api.call("GET", f"/properties/{card['id']}/gallery")
        entry["gallery"] = sorted([g["id"], g.get("room"), bool(g.get("is_gallery_cover"))] for g in gallery)
        listings[str(card["id"])] = entry
    return listings


def check_existing(api, out):
    report = Report()
    _, health = api.call("GET", "/health")
    report.check("backend is up", health is not None)
    listings = public_state(api)
    report.check(f"published listings found: {len(listings)}", True)
    for listing_id, entry in listings.items():
        name = f"{entry['title']!r}"
        if entry["tour"]:
            report.check(f"{name}: single 360 tour page served", entry["tour_is_page"], entry.get("tour_status"))
            report.check(f"{name}: single panorama image served", entry["tour_image_status"] == 200)
        for room in entry["rooms"]:
            status, page = api.call("GET", room["tour"], expect=(200, 404))
            report.check(f"{name}: room tour {room['room']!r} served", status == 200)
        report.check(f"{name}: gallery readable ({len(entry['gallery'])} photos)", True)
    path = out / f"existing-{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"listings": listings, "results": report.results}, indent=2), encoding="utf-8")
    print(f"Saved {path}")
    return 1 if report.failed else 0


def login(api, role):
    email = input(f"{role} account email: ").strip()
    password = getpass.getpass(f"{role} account password (hidden): ")
    _, data = api.call("POST", "/auth/staff/login", body={"email": email, "password": password})
    return data["access_token"]


def create(api, out, apply):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    title = f"[QA] Room panoramas {stamp}"
    plan = [
        f"create draft listing {title!r} (agent account)",
        "upload cover, single (legacy) 360 panorama, gallery photos Kitchen + Hall (cover = Kitchen)",
        "upload room 360 panoramas: Bedroom 1, Bedroom 2, Living Room",
        "re-upload ' bedroom   1 ' (other case/spacing): must replace Bedroom 1, not add",
        "remove Living Room; removing it again must give 404",
        "submit (agent), approve (admin account)",
        "check customers see: the listing, its single tour, exactly 2 room tours with the right images, 2 gallery photos",
    ]
    print("Plan:" + "".join(f"\n  - {step}" for step in plan))
    if not apply:
        print("Nothing done. Add --apply to run it (writes to the development database).")
        return 0

    report = Report()
    images = {label: labelled_image(label, 2048, 1024) for label in
              ("single", "Bedroom 1", "Bedroom 2", "Living Room", "bedroom 1 (replacement)")}
    images.update({label: labelled_image(label, 1200, 800) for label in ("cover", "Kitchen", "Hall")})
    before = public_state(api)

    api.token = login(api, "Agent")
    payload = {"title": title, "property_type": "flat", "locality": "QA test",
               "city": "Visakhapatnam", "price": 1, "area_sqft": 100, "bedrooms": 2,
               "description": "Automated functional test listing; safe to delete.",
               "listing_consent": True}
    _, listing = api.call("POST", "/staff/properties", body=payload, expect=(201,))
    listing_id, version = listing["id"], listing["version"]
    path = f"/staff/properties/{listing_id}"
    (out / f"qa-listing-{stamp}.txt").write_text(f"{listing_id}\n{title}\n", encoding="utf-8")
    print(f"Created {listing_id}")

    def step(method, url, **kwargs):
        nonlocal version
        status, data = api.call(method, url, query={"version": version, **kwargs.pop("query", {})}, **kwargs)
        version = data["version"]
        return data

    step("PUT", f"{path}/media/cover", raw=images["cover"])
    step("PUT", f"{path}/media/panorama", raw=images["single"])
    photos = [step("POST", f"{path}/gallery", raw=images[room], query={"room": room}, expect=(201,))["id"]
              for room in ("Kitchen", "Hall")]
    step("PUT", f"{path}/gallery/{photos[0]}/cover")
    for room in ("Bedroom 1", "Bedroom 2", "Living Room"):
        step("PUT", f"{path}/panoramas/{urllib.parse.quote(room)}", raw=images[room])
    _, rooms = api.call("GET", f"{path}/panoramas")
    report.check("staff: three room panoramas listed",
                 [r["room"] for r in rooms] == ["Bedroom 1", "Bedroom 2", "Living Room"], rooms)
    first_id = {r["room"]: r["id"] for r in rooms}["Bedroom 1"]
    replaced = step("PUT", f"{path}/panoramas/{urllib.parse.quote(' bedroom   1 ')}",
                    raw=images["bedroom 1 (replacement)"])
    _, rooms = api.call("GET", f"{path}/panoramas")
    report.check("staff: other case/spacing replaced Bedroom 1 (still three rooms, same id)",
                 len(rooms) == 3 and replaced["id"] == first_id, rooms)
    step("DELETE", f"{path}/panoramas/{urllib.parse.quote('Living Room')}")
    status, _ = api.call("DELETE", f"{path}/panoramas/{urllib.parse.quote('Living Room')}",
                         query={"version": version}, expect=(404,))
    report.check("staff: removing an absent room gives 404", status == 404)
    _, gallery = api.call("GET", f"{path}/gallery")
    report.check("staff: gallery still exactly the two photos (room panoramas not in it)",
                 sorted(g["id"] for g in gallery) == sorted(photos), gallery)
    status, single = api.call("GET", f"{path}/media/panorama")
    report.check("staff: single panorama still present and unchanged", same_colour(single, "single"))

    _, submitted = api.call("POST", f"{path}/submit", body={"version": version})
    api.token = login(api, "Admin")
    api.call("POST", f"{path}/approve", body={"version": submitted["version"]})
    api.token = None

    after = public_state(api)
    mine = after.get(str(listing_id))
    report.check("customer: listing is public", mine is not None)
    if mine:
        report.check("customer: single tour served and shows the single panorama",
                     mine["tour_is_page"] and mine["tour_image_colour"] is not None
                     and all(abs(a - b) <= 30 for a, b in zip(mine["tour_image_colour"], COLOURS["single"])))
        report.check("customer: exactly two room tours",
                     sorted(r["room"].lower() for r in mine["rooms"]) == ["bedroom 1", "bedroom 2"], mine["rooms"])
        for room in mine["rooms"]:
            status, page = api.call("GET", room["tour"], expect=(200, 404))
            _, image = api.call("GET", f"/property-media/{room['id']}")
            expected = "bedroom 1 (replacement)" if room["room"].lower() == "bedroom 1" else "Bedroom 2"
            report.check(f"customer: room tour {room['room']!r} served with its own image",
                         status == 200 and same_colour(image, expected), dominant_colour(image))
        report.check("customer: gallery shows the two photos only",
                     sorted(g[0] for g in mine["gallery"]) == sorted(photos), mine["gallery"])
    others_before = {k: v for k, v in before.items()}
    others_after = {k: v for k, v in after.items() if k != str(listing_id)}
    report.check("existing listings unchanged", others_before == others_after)

    result = {"listing_id": str(listing_id), "title": title, "results": report.results,
              "before": before, "after": after}
    report_path = out / f"qa-run-{stamp}.json"
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved {report_path}")
    print("\nTo remove the test listing later (needs approval; as postgres, one transaction):")
    print(cleanup_sql(listing_id, title))
    print("RESULT: " + ("OK" if not report.failed else f"{len(report.failed)} check(s) FAILED"))
    return 1 if report.failed else 0


def cleanup_sql(listing_id, title):
    quoted = title.replace("'", "''")
    return f"""BEGIN;
DO $$ BEGIN
  IF (SELECT count(*) FROM public.properties
      WHERE id = '{listing_id}' AND title = '{quoted}') <> 1 THEN
    RAISE EXCEPTION 'QA listing not found exactly once; nothing deleted.';
  END IF;
END $$;
DELETE FROM public.properties WHERE id = '{listing_id}' AND title = '{quoted}';  -- media and private details cascade
COMMIT;"""


def compare(before_path, after_path, ignore):
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))["listings"]
    after = {k: v for k, v in json.loads(Path(after_path).read_text(encoding="utf-8"))["listings"].items()
             if k not in (ignore or [])}
    differences = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    for key in differences:
        print(f"DIFFERENT  {key}: {before.get(key, {}).get('title') or after.get(key, {}).get('title')}")
    print("Existing listings unchanged." if not differences else f"{len(differences)} listing(s) differ.")
    return 1 if differences else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Development backend URL.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check-existing", "create"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True, help="Folder for reports.")
        if name == "create":
            p.add_argument("--apply", action="store_true", help="Actually create the QA listing.")
    p = sub.add_parser("compare")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--ignore", action="append", help="Listing id to leave out (the QA listing).")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return compare(args.before, args.after, args.ignore)
    args.out.mkdir(parents=True, exist_ok=True)
    api = Api(args.api)
    if args.command == "check-existing":
        return check_existing(api, args.out)
    return create(api, args.out, args.apply)


if __name__ == "__main__":
    sys.exit(main())
