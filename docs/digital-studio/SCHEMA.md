# Digital Studio database schema

Migration: `backend/migrations/010_marketing_jobs.sql` (additive; grants
`SELECT, INSERT, UPDATE` to `leia_app` when that role exists).

## `outbox_events`

Durable domain events written in the same transaction as the state change.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `event_type` | text | `property.published` only (CHECK) |
| `aggregate_id` | uuid → `properties.id` | |
| `aggregate_version` | int | property version after approval |
| `payload` | jsonb | `{property_id, property_version}` |
| `created_at` | timestamptz | |
| `dispatched_at` | timestamptz null | set by a future dispatcher |

Unique: `(event_type, aggregate_id, aggregate_version)`.
Index: pending events `(created_at) WHERE dispatched_at IS NULL`.

## `marketing_jobs`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `property_id` | uuid → `properties.id` | |
| `property_version` | int | approved version the snapshot was taken from |
| `job_type` | text | `reel_v1` only (CHECK) |
| `status` | text | `queued, processing, awaiting_review, approved, publishing, published, failed, cancelled` |
| `property_snapshot` | jsonb | see below; never edited |
| `source_event_id` | uuid → `outbox_events.id` null | |
| `attempts` / `max_attempts` | int | worker retry budget (default 3) |
| `last_error` | text ≤ 500 | sanitised single line |
| `next_attempt_at`, `locked_by`, `locked_until` | | reserved for M4 worker leasing |
| `created_by` | uuid → `users.id` null | approving admin |
| `created_at`, `updated_at` | timestamptz | |
| `version` | int | optimistic concurrency for API actions |

Unique: `(property_id, job_type, property_version)`.
Indexes: `(status, next_attempt_at)`, `(property_id)`, `(created_at DESC, id DESC)`.

### `property_snapshot` (schema 1)

```json
{
  "schema": 1,
  "property_id": "uuid",
  "property_version": 4,
  "approved_at": "2026-11-24T10:00:00+00:00",
  "property": { "title": "…", "property_type": "flat", "price": 6500000,
                "bedrooms": 2, "area_sqft": 1180, "facing": "east",
                "locality": "…", "city": "…", "…": "all public CRM fields" },
  "agent": { "id": "uuid", "name": "…" },
  "media": [ { "id": "uuid", "kind": "photo", "room": "Kitchen", "is_gallery_cover": true } ]
}
```

Private CRM fields (`owner_name`, `owner_phone`, `listing_consent`, `agent_notes`)
are excluded by construction and by test.

## `marketing_job_transitions`

Audit trail of every status change.

| Column | Type |
|---|---|
| `id` | uuid PK |
| `job_id` | uuid → `marketing_jobs.id` |
| `from_status` | text null (null = created) |
| `to_status` | text |
| `actor_id` | uuid → `users.id` null (null = system) |
| `reason` | text ≤ 500 |
| `created_at` | timestamptz |

## Rollback

Code can be rolled back while leaving these tables in place. To remove them
entirely (data loss): drop `marketing_job_transitions`, `marketing_jobs`,
`outbox_events` in that order and delete `010_marketing_jobs.sql` from
`crm_schema_migrations`.
