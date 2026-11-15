# F3: bring D:\LEIA up to date with GitHub (plan, not executed)

`D:\LEIA` is on `codex/customer-quality-checks` at **`23b25c0`** (merge of PR #1). GitHub's
branch is at **`e99ffc3`**: **18 commits ahead** (four merged pull requests, #2–#5), and
`D:\LEIA` has no commits GitHub lacks. The app files checked so far (`backend/main.py`,
`backend/property_media.py`, customer `PropertyView.tsx`, agent `PropertyMedia.tsx`) are
identical to `23b25c0`; `leia-review.diff` is an untracked file in the folder.

## What the 18 commits change

| Area | Change | Effect on the running apps |
| --- | --- | --- |
| Customer app | `PropertyView.tsx` uses the tested helper `src/lib/roomTours.ts` | Room tours with an unexpected address are ignored (hardening); otherwise the same |
| Backend | new `requirements-runtime.txt` (`-r requirements-test.txt` + `uvicorn>=0.30,<1`) | None at runtime; documents the server dependencies |
| Tests | `test_crm.py` (+69), `test_property_media.py` (+48), `frontend/tests/roomTours.test.mjs` | None |
| CI / deploy | `crm-checks.yml`, new `leia-ec2-delivery.yml` (manual start only), `deploy/ec2/deploy-release.sh`, `docs/EC2_DELIVERY.md` | None locally; nothing deploys by itself |

No `package.json` changes: no `npm install` needed for either app.

## Plan (each phase needs the previous one to pass)

**Phase 0: inventory (read-only; no file in the folder changes).**

```powershell
git -C D:\LEIA status --short --branch          # any M/A/D lines = local edits: stop and review
git -C D:\LEIA stash list
git -C D:\LEIA worktree list
git -C D:\LEIA fetch origin                      # updates GitHub's copy only, not your files
git -C D:\LEIA log --oneline origin/codex/customer-quality-checks..HEAD   # must print nothing
git -C D:\LEIA log --oneline HEAD..origin/codex/customer-quality-checks   # expect the 18 commits
```

**Phase 1: copy of the working folder** (keeps `.env` files and untracked files):

```powershell
robocopy D:\LEIA "D:\LEIA-local-backups\LEIA-$(Get-Date -Format yyyyMMdd_HHmm)" /E /XD node_modules .venv .expo
```

**Phase 2 (approval required): fast-forward only.** Stop the backend and Expo first.

```powershell
git -C D:\LEIA merge --ff-only origin/codex/customer-quality-checks
```

Git refuses, changing nothing, if any local edit would be overwritten or if the histories
diverged. Untracked files (`leia-review.diff`, `.env`) are never touched.

**Phase 3: dependencies.** `D:\LEIA\backend\.venv\Scripts\pip.exe install -r D:\LEIA\backend\requirements-runtime.txt`
(only uvicorn is pinned; most likely already installed).

**Phase 4: checks.** Start the backend; run the read-only
`room_panorama_check.py check-existing` and compare with the last baseline; in
`D:\LEIA\frontend`: `node --experimental-strip-types --test tests/roomTours.test.mjs`; open
a listing with a 360° tour in the customer app.

**Undo (approval required):** `git -C D:\LEIA reset --keep 23b25c0` moves the branch back
and refuses if it would lose local edits; or restore files from the Phase 1 copy.

## Order with the other work

1. Finish the functional test on the current code (`23b25c0`), so its evidence matches what runs.
2. Sync (this plan).
3. F1/F2 (`fix/room-tours-whole-home`, based on `e99ffc3`) reach `D:\LEIA` only after their
   pull request is reviewed and merged into `codex/customer-quality-checks`, then another
   fast-forward like Phase 2.
