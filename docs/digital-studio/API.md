# Digital Studio API (M1)

Base path `/staff/marketing`. Same bearer access tokens as the Agent CRM. Every
response sets `Cache-Control: no-store`.

| Role | Access |
|---|---|
| anonymous | 401 |
| customer | 403 |
| agent | read jobs for listings they created; others look like 404 |
| admin | read all jobs, retry, cancel, reconcile |

## `GET /jobs`

Query: `status` (one of the job statuses), `property_id` (uuid),
`before` (job id from the previous page), `limit` (1–100, default 50).
Newest first.

```json
[{
  "id": "uuid", "property_id": "uuid", "property_version": 4,
  "property_title": "Test Studio Residency", "job_type": "reel_v1",
  "status": "queued", "attempts": 0, "max_attempts": 3, "last_error": null,
  "created_at": "…", "updated_at": "…", "version": 1
}]
```

## `GET /jobs/{job_id}`

Summary fields plus `property_snapshot`, `source_event_id` and `transitions`
(`from_status`, `to_status`, `actor_id`, `reason`, `created_at`, oldest first).
404 if missing or outside the caller's scope.

## `POST /jobs/{job_id}/retry` (admin)

Body `{"version": <current job version>}`. Allowed only from `failed`. Resets
`attempts` to 0, clears `last_error`, records the previous error in the
transition reason, and returns the job to `queued`.

## `POST /jobs/{job_id}/cancel` (admin)

Body `{"version": …}`. Allowed from `queued`, `awaiting_review`, `approved`
or `failed`.

Both actions: 404 unknown job, 409 stale version or illegal transition,
422 invalid body.

## `POST /reconcile` (admin)

Creates jobs for approved, available listings that have none for their current
version. Idempotent. Response `{"created": 0}`.

## Side effect on an existing endpoint

`POST /staff/properties/{id}/approve` is unchanged for callers. It now also
writes a `property.published` event and a queued job; any failure there is
swallowed and logged, and the approval still succeeds.
