# Digital Studio architecture

## Business flow

```
Agent CRM ─ submit ─▶ Admin approve ─▶ property approved (Customer App search)
                                   └─▶ property.published event + queued marketing job
                                          ▼  (M4) render worker ─▶ awaiting_review
                                          ▼  (M5) human approval ─▶ approved
                                          ▼  (M6) publishing ─▶ Instagram / Facebook
```

The agent enters property details once, in the CRM. Digital Studio only reads the
approved listing; it never asks for the same information again.

## M1: approval → marketing job

`POST /staff/properties/{id}/approve` (admin) runs `crm.transition_property`:

1. `SELECT … FOR UPDATE` on the property; require `status = 'submitted'` and the
   expected `version`.
2. `UPDATE properties SET status = 'approved', version = version + 1 …`
3. `marketing_jobs.enqueue_on_publish(connection, property_id, admin_id)`:
   - opens a **SAVEPOINT** (`connection.transaction()` inside the open transaction);
   - builds a snapshot of the approved listing (public fields, agent name, media ids);
   - inserts a `property.published` row into `outbox_events`
     (`ON CONFLICT … DO NOTHING`);
   - inserts a `queued` `reel_v1` row into `marketing_jobs`
     (`UNIQUE (property_id, job_type, property_version)`, `ON CONFLICT DO NOTHING`);
   - records the `NULL → queued` transition.
4. The approval transaction commits.

### Failure isolation

Any exception inside step 3 rolls back **only the savepoint** and is logged
(property id and exception class, no listing data). The approval in step 2 still
commits, so the listing is published to customers. This is covered by a test that
takes the `marketing_jobs` table offline during an approval.

`MARKETING_JOBS_ENABLED=false` skips step 3 entirely.

### Missed jobs

`reconcile` finds approved, available listings without a job for their current
version and enqueues them, each under its own savepoint. Run it via
`POST /staff/marketing/reconcile` (admin) or `python marketing_jobs.py reconcile`.

### Duplicate prevention

- The property row lock serialises concurrent approvals; only one can move
  `submitted → approved` (tested with four concurrent approvals).
- Unique constraints on `outbox_events (event_type, aggregate_id, aggregate_version)`
  and `marketing_jobs (property_id, job_type, property_version)` make enqueueing
  idempotent for the approval path and the reconciler alike.
- Approved listings are immutable in the CRM (edits and media changes are
  draft-only), so one approved version means one job.

## Data the studio may use

The snapshot stored on each job is the **only** source for scripts, captions and
on-screen text. It contains the CRM's public listing fields, the creating agent's
id and name, and media ids. It never contains `owner_name`, `owner_phone`,
`listing_consent` or `agent_notes`.

Not yet available and therefore never to be invented: verified agent phone,
walkthrough videos, a stored nearby-places list. See ROADMAP M2.

## Status model

```
queued ─▶ processing ─▶ awaiting_review ─▶ approved ─▶ publishing ─▶ published
   │           │  ▲            │   │           │            │
   │           ▼  └─(lease)    │   └▶ queued   │            ▼
   │         failed ◀──────────┼───────────────┼─────── failed
   │           │               ▼               ▼
   └──────▶ cancelled ◀────────┴───────────────┘
failed ─(admin retry)─▶ queued
```

The table lives in `marketing_models.ALLOWED_TRANSITIONS`; every change goes
through `marketing_jobs.transition`, which locks the job, checks the optional
version and writes `marketing_job_transitions`. Publishing is reachable only
from `approved`, i.e. after a human approved the video.

## Why one repository and one database

Approval and job creation must share a transaction, the snapshot reads listing
and media tables directly, and the CRM's auth/RBAC is reused unchanged. Heavy work
(FFmpeg, TTS) will run in a separate **worker process** deployed from this same
repository (M4), so the API stays responsive.

## Future: AI Marketing Agent (M7)

An agent will orchestrate the same tools the pipeline uses: read the snapshot,
pick a template, write the 3C script, select media, call voiceover/captions,
render, self-check, then stop at `awaiting_review`. Human approval and publishing
stay outside the agent's authority.
