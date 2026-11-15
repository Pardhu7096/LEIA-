# Functional test: room-by-room 360° panoramas (development)

Status: **prepared, not run on the development database.** The read-only part may be
run at any time; everything that creates or deletes data needs the owner's approval.

## What is tested

The code that actually runs on the development machine: `D:\LEIA` on
`codex/customer-quality-checks` at **`23b25c0`** (11 commits behind GitHub's
`e99ffc3`; files identical to `23b25c0`, no local edits). Its backend media code is
identical to the code the 007 tests ran against. Its customer app shows room tours
(`PropertyView.tsx`) without the later tour-URL check from `roomTours.ts`.

Database: `leia` after migration 007 (step 5, 14 Nov 19:07; history 000–007).

## 0. Before anything that writes

1. Fresh verified backup (restore-tested), backend may stay running:
   `powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step1-backup-and-check.ps1 -DropScratchAfterVerify`
2. Start the backend from `D:\LEIA\backend` as usual (port 8000 assumed).

## 1. Automated API check (`scripts/functional/room_panorama_check.py`)

Run with the backend's Python from the audit worktree:

```powershell
$PY = "D:\LEIA\backend\.venv\Scripts\python.exe"
$QA = "D:\LEIA-schema-audit\scripts\functional\room_panorama_check.py"
$OUT = "D:\LEIA-backups\room-panorama-test"

& $PY $QA check-existing --out $OUT          # read-only: public GETs only, no login
& $PY $QA create --out $OUT                  # prints the plan; changes nothing
& $PY $QA create --out $OUT --apply          # APPROVAL NEEDED: creates one QA listing
& $PY $QA check-existing --out $OUT          # second read-only snapshot
& $PY $QA compare <first existing-*.json> <second existing-*.json> --ignore <QA listing id>
```

`create --apply` asks for an agent and an admin login (passwords hidden, never stored)
and creates **one** listing titled `[QA] Room panoramas <time>`. It uploads labelled,
distinctly coloured test images and checks, decoding the served images:

| # | Check |
| --- | --- |
| 1 | Cover, single (legacy) 360°, gallery photos Kitchen and Hall upload; Kitchen is the gallery cover |
| 2 | Room 360°s Bedroom 1, Bedroom 2, Living Room are listed for staff |
| 3 | Re-uploading ` bedroom   1 ` replaces Bedroom 1 (same id, still three rooms) |
| 4 | Removing Living Room works; removing it again gives 404 |
| 5 | Gallery still exactly two photos (room panoramas never appear in it) |
| 6 | The single panorama is unchanged |
| 7 | After submit and admin approval the listing is public |
| 8 | Customers get the single tour with the single panorama |
| 9 | Customers get exactly two room tours, each serving its own image |
| 10 | Customer gallery shows the two photos only |
| 11 | Every other published listing is unchanged |

The read-only `check-existing` covers existing listings: each single tour page and
image is served, room tours (none expected yet) and galleries are readable.

Rehearsed against the `23b25c0` backend (uvicorn, PostgreSQL 18.6, a published
listing with a single panorama like the development data): all checks pass; the
printed cleanup SQL removed the QA listing with its 6 media rows, refuses a second
run, and the existing listing compared unchanged.

## 2. Customer app (manual, Expo, against the same backend)

| # | Do | Expect |
| --- | --- | --- |
| C1 | Open an existing listing that has only a single 360° | Tour tab shows that panorama; no room buttons |
| C2 | Open the QA listing, Tour tab | Room buttons `Bedroom 2`, `bedroom 1`; the first is shown |
| C3 | Tap each room button | The viewer shows that room's labelled image (orange-red "BEDROOM 1 (REPLACEMENT)", purple "BEDROOM 2") |
| C4 | Look for the single (blue "SINGLE") panorama on the QA listing | **Not reachable** (see finding F1) |
| C5 | Photos tab of the QA listing | Exactly two photos (Kitchen, Hall); no panoramas among them |
| C6 | Signed out | Same as C2–C5 (public listing) |
| C7 | After cleanup | QA listing gone; existing listings as in C1 |

## 3. Agent app (manual)

| # | Do | Expect |
| --- | --- | --- |
| A1 | Open the QA listing (draft stage, before approval) | "360° room panoramas" lists each room with a remove button |
| A2 | Upload for a room with nothing selected | Button disabled / "Select a room…" message |
| A3 | Legacy panorama section | Shown as a preview only; no replace or remove (finding F1) |

## 4. Cleanup (approval needed)

`create --apply` prints a guarded SQL block for its listing id and title: one
transaction that deletes nothing unless exactly that listing exists; media and
private details are removed with it. Run it as `postgres` with `psql -v ON_ERROR_STOP=1`.
Sign-in sessions and rate-limit rows created by the test logins are normal app data.

## Findings so far (code review and rehearsal)

| ID | Severity | Finding |
| --- | --- | --- |
| F1 | Medium (product) | Once a listing has any room panorama, the customer app shows only room tours (first room by default); the listing's single panorama is still stored and served by the API but no longer reachable in the app. The agent app shows the single panorama as a preview only and cannot replace or remove it, so it cannot be cleaned up either. Decide: show it as an extra "Whole home" tour, or treat it as replaced and let agents remove it. |
| F2 | Low | (L10) Re-uploading a room in other letter case renames it to the latest spelling, and rooms sort case-sensitively (`Bedroom 2` before `bedroom 1`). |
| F3 | Low | The development checkout `D:\LEIA` is 11 commits behind its GitHub branch: missing there are the customer tour-URL check (`roomTours.ts`), configurable API URLs (`apiUrl.ts`) and the backend hardening on the audit branch. Testing uses what runs (`23b25c0`). |
| F4 | Info | New single panoramas can no longer be uploaded from the agent app (rooms only); the API still accepts them. |
