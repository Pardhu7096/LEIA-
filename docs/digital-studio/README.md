# LEIA Digital Studio

Digital Studio turns approved LEIA listings into marketing videos. It lives inside
the LEIA repository and shares its PostgreSQL database, authentication and RBAC.

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Flow from CRM approval to marketing job, failure isolation |
| [SCHEMA.md](SCHEMA.md) | `outbox_events`, `marketing_jobs`, `marketing_job_transitions` |
| [API.md](API.md) | `/staff/marketing` endpoints and permissions |
| [ROADMAP.md](ROADMAP.md) | Remaining milestones M2–M7 |

## Status (M1)

| Component | State |
|---|---|
| `property.published` outbox event on admin approval | Implemented, tested |
| Idempotent `reel_v1` marketing job per approved property version | Implemented, tested |
| Approval can never fail because of marketing (savepoint) | Implemented, tested |
| Reconciler for missed jobs (API + CLI) | Implemented, tested |
| Job status tracking, transition history, retry, cancel | Implemented, tested |
| RBAC: agents see own listings' jobs; admins manage all | Implemented, tested |
| Render worker, voiceover, captions, dashboard, publishing | Not started (see roadmap) |

An earlier "Video Studio M1" (reported as `tools/leia_video_studio/`, 228 tests)
could not be found on any accessible machine or repository and is **not** part of
this code. If it is recovered, compare it with M3/M4 before building those.

## Files

- `backend/migrations/010_marketing_jobs.sql`: schema
- `backend/marketing_jobs.py`: snapshot, enqueue, reconcile, transitions, router, CLI
- `backend/marketing_models.py`: API models and the status transition table
- `backend/crm.py`: calls `enqueue_on_publish` inside the approval transaction
- `backend/tests/test_marketing_jobs.py`: unit and integration tests

## Apply the migration

```powershell
cd D:\LEIA\backend
.\.venv\Scripts\python.exe .\migrate_crm.py --owner postgres
```

The migration is additive and tracked; rerunning it is safe. Check which database
`DATABASE_URL` points at before running it: `backend/.env` points at staging.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `MARKETING_JOBS_ENABLED` | `true` | `false`/`0`/`no`/`off` stops automatic job creation on approval. Approval is unaffected; run the reconciler later to backfill. |

## Run the tests

Unit tests run without a database. Integration tests need an **empty, local**
database named `leia_marketing_test` (or `crm_test`); `tests/conftest.py` blocks
every other connection.

```powershell
cd D:\LEIA\backend
$env:TEST_DATABASE_URL = "postgresql://USER:PASSWORD@localhost:5432/leia_marketing_test"
.\.venv\Scripts\python.exe -m pytest tests/test_marketing_jobs.py -q
```

Drop and recreate the database before each integration run.

## Backfill missing jobs

```powershell
.\.venv\Scripts\python.exe .\marketing_jobs.py reconcile
```

or, as an admin, `POST /staff/marketing/reconcile`. Both are idempotent.

## Never commit

Credentials, `.env` files, database dumps, raw property footage, generated videos
and audio, caches and virtual environments. `.gitignore` covers these.

## M2 walkthrough and contact intake

Apply migration `012_studio_inputs.sql` before deploying the API. On the
backend host, install `ffprobe` and configure `STUDIO_S3_BUCKET` for a private
S3-compatible bucket. Set `STUDIO_S3_ENDPOINT_URL` for a compatible provider
that requires a custom endpoint. Use the standard AWS SDK credential and
region settings. The API writes objects under `walkthroughs/`. The render
worker will need read access to this prefix.

1. An admin confirms the agent's number and consent directly, then sends
   `PUT /staff/marketing/agents/{agent_id}/verified-contact` with an
   international phone number in `{"phone":"+919876543210"}`. Send
   `{"phone":null}` to clear the number for future snapshots.
2. The assigned agent or an admin sends the MP4 as the request body to
   `PUT /staff/marketing/properties/{property_id}/walkthrough?version={version}`
   with `Content-Type: video/mp4`. Limits are 100 MiB and 10 minutes. Use
   the returned listing version for subsequent draft actions.
3. Submit and approve the draft through the existing CRM workflow. Check
   the job detail for `agent.phone`, `agent.phone_verified_at`, and a media
   entry with `kind: walkthrough`. The walkthrough ID maps to
   `walkthroughs/{id}.mp4` in the private bucket.

Approval captures the current inputs. Existing job snapshots retain their
original inputs. Replacing footage uses a new object key so older snapshots
can still resolve their media.

## Standalone admin workspace

Open `/studio/` on the LEIA backend host and sign in with a LEIA admin
account. Choose a draft under **Listing inputs** to upload the walkthrough
and record the verified number for the listing agent. After listing
approval, open its job under **Marketing jobs** to verify the snapshot.
The workspace also supports retrying failed jobs and cancelling jobs
where the current status allows it.
