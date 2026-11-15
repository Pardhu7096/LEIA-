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

## 0. Run (approved 14 Nov 19:24: backup, baseline, one QA listing; no deletion)

1. Verified backup (restore-tested), today:
   `powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step1-backup-and-check.ps1 -DropScratchAfterVerify`
   and note its stamp (`step1-<stamp>.log` ends with `RESULT: OK`).
2. Start the backend from `D:\LEIA\backend` as usual on `http://127.0.0.1:8000`.
3. `git -C D:\LEIA-schema-audit pull --ff-only`, then
   `powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step6-functional-test.ps1 -BackupStamp <stamp>`

Step 6 refuses, before any change, unless: the worktree is the reviewed version; the
backup is today's, its step-1 log says `RESULT: OK` and its SHA-256 matches; the backend
answers on 127.0.0.1/localhost; and no QA listing was created before. It then runs the
read-only baseline, the test (one QA listing), a second baseline and the comparison of all
other listings, and writes `D:\LEIA-backups\step6-<time>.log`. It never deletes.

## 1. What the automated test does (`scripts/functional/room_panorama_check.py`)

Accepts only a backend on this computer (`http://127.0.0.1` / `http://localhost`).

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
| C2 | Open the QA listing, Tour tab | Current code: room buttons `Bedroom 2`, `bedroom 1` (F2 order), first shown. With F1/F2: `Whole Home`, `Bedroom 1`, `Bedroom 2` |
| C3 | Tap each room button | The viewer shows that room's labelled image (orange-red "BEDROOM 1 (REPLACEMENT)", purple "BEDROOM 2") |
| C4 | Look for the single (blue "SINGLE") panorama on the QA listing | With the current code (`23b25c0`): **not reachable** (F1). With the F1 fix: a "Whole Home" button, shown first |
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

## Decisions (14 Nov 19:24) and status

| ID | Decision | Status |
| --- | --- | --- |
| F1 | Keep the single panorama visible as "Whole Home" next to every room | Fixed on branch `fix/room-tours-whole-home` (customer app only; no media changes); not merged |
| F2 | Consistent room names, natural order; keep stored media and case-insensitive uniqueness | Fixed on the same branch (backend: replace keeps the first spelling; natural, case-insensitive lists); not merged. When it reaches the audit branch, `test_migration_007.py` must expect the first spelling. |
| F3 | Audit and plan only | `docs/DEV_CHECKOUT_SYNC_PLAN.md` |
| F4 | Deferred to a later release; single panoramas stay viewable | No change |

## Step 6 stopped at the admin login (14 Nov 19:56) and how to resume

`step6-20261114_195648.log`: backup, baseline and the agent-side steps ran; the QA listing
`0d9b999a-0a1c-4ed7-badb-2277cd9d84cd` was created, given its media and submitted; the admin
login then got `401 Incorrect email or password`. Nothing after that ran (no approval,
no customer checks, no report).

**Roles.** `users.role` is `customer`, `agent` or `admin` (CHECK constraint); emails are
stored in lower case (CHECK `email_lowercase`) and logins lower-case what is typed.
Public registration always makes a `customer`; agents and admins exist only where the role
was set in the database. Agents and admins use the same staff login (`/auth/staff/login`):
a wrong email or password gives **401**; a correct customer login gives **403**. Agents see
and edit only their own listings and can submit them; only admins can approve
(`require_admin`) and see every listing. The role is re-read from the database on every
request. So a 401 at the admin prompt means the email typed does not exist or the password
is wrong, not that the account lacks the admin role (that would have failed later, at approval, with 403).

**1. Read-only diagnostic** (changes nothing; staff email and role only, no passwords or hashes):
`powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-diag-staff-and-qa.ps1 -QaId 0d9b999a-0a1c-4ed7-badb-2277cd9d84cd`
Expected for a resumable listing: status `submitted`, not reviewed; media: cover, panorama,
`panorama_room` x2 (bedroom 1, Bedroom 2), photo x2 (Kitchen = gallery cover, Hall); one [QA] listing.

**2. Resume (needs approval; same QA listing, nothing created or deleted):**
`powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step6b-resume.ps1 -BackupStamp <today's step-1 stamp> -QaId 0d9b999a-0a1c-4ed7-badb-2277cd9d84cd`
Before any change it checks the worktree, today's backup, the local backend, the qa-listing
file and stopped log naming this id, no earlier report, and that every published listing
equals the 19:56 baseline. A refused login (3 tries), a non-admin account, or a listing not in
the expected state stops with "STOPPED BEFORE ANY CHANGE". Otherwise it approves only the QA
listing and runs the customer checks and the comparison of every other listing.
