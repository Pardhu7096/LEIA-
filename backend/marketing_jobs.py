"""Digital Studio M1: marketing jobs created from approved CRM listings.

Approving a property calls ``enqueue_on_publish`` inside the approval
transaction. It writes a ``property.published`` outbox event and a queued
``reel_v1`` marketing job under a SAVEPOINT: if anything fails, only the
savepoint is rolled back and the approval still commits. ``reconcile`` later
creates any job that is missing, so a skipped job is never lost.

Run the reconciler from a shell (uses DATABASE_URL, so check which database
it points at first):  python marketing_jobs.py reconcile
"""

import json
import logging
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg import sql
from psycopg.types.json import Jsonb

from auth import require_admin, require_staff
from database import get_connection
from marketing_models import (
    JobStatus,
    JobVersion,
    MarketingJobDetails,
    MarketingJobSummary,
    ReconcileResult,
    can_transition,
)
from property_models import PropertyDraft

log = logging.getLogger("leia.marketing")

JOB_TYPE = "reel_v1"
EVENT_TYPE = "property.published"
SNAPSHOT_SCHEMA = 1
# Owner identity, consent and internal notes never leave the CRM.
PRIVATE_FIELDS = frozenset({
    "owner_name", "owner_phone", "listing_consent", "agent_notes",
    # GEO-M1 coordinate provenance is internal review data, not marketing content.
    "location_source", "location_accuracy_m", "location_captured_at",
})
# Field names come only from our model, never from request input.
SNAPSHOT_FIELDS = tuple(
    name for name in PropertyDraft.model_fields if name not in PRIVATE_FIELDS
)
ERROR_LIMIT = 500


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    pass


def enabled() -> bool:
    value = os.getenv("MARKETING_JOBS_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def clean_error(text) -> str | None:
    """Single-line, control-character free, at most ERROR_LIMIT characters."""
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(text))).strip()
    return cleaned[:ERROR_LIMIT] or None


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def build_snapshot(cursor, property_id):
    """Verified facts for an approved listing, or None if it is not approved."""
    cursor.execute(
        sql.SQL("""
            SELECT {}, p.version, p.status, p.reviewed_at, p.created_by,
                u.name AS agent_name
            FROM public.properties AS p
            LEFT JOIN public.users AS u ON u.id = p.created_by
            WHERE p.id = %s;
        """).format(
            sql.SQL(", ").join(sql.Identifier("p", name) for name in SNAPSHOT_FIELDS)
        ),
        (property_id,),
    )
    row = cursor.fetchone()
    if row is None or row["status"] != "approved":
        return None
    cursor.execute(
        """SELECT id, kind, room, is_gallery_cover FROM public.property_media
           WHERE property_id = %s ORDER BY uploaded_at, id;""",
        (property_id,),
    )
    media = [dict(item) for item in cursor.fetchall()]
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "property_id": property_id,
        "property_version": row["version"],
        "approved_at": row["reviewed_at"],
        "property": {name: row[name] for name in SNAPSHOT_FIELDS},
        # Only the agent's name is stored: LEIA has no verified agent phone yet.
        "agent": {"id": row["created_by"], "name": row["agent_name"]},
        "media": media,
    }
    return json.loads(json.dumps(snapshot, default=_json_default))


def record_transition(cursor, job_id, from_status, to_status, actor_id, reason):
    cursor.execute(
        """INSERT INTO public.marketing_job_transitions
           (job_id, from_status, to_status, actor_id, reason)
           VALUES (%s, %s, %s, %s, %s);""",
        (job_id, from_status, to_status, actor_id, clean_error(reason)),
    )


def enqueue(cursor, property_id, actor_id, reason):
    """Create the outbox event and job once per approved property version.

    Returns the new job id, or None when the listing is not approved or a job
    for this version already exists.
    """
    snapshot = build_snapshot(cursor, property_id)
    if snapshot is None:
        return None
    version = snapshot["property_version"]
    cursor.execute(
        """INSERT INTO public.outbox_events
               (event_type, aggregate_id, aggregate_version, payload)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT ON CONSTRAINT outbox_events_once DO NOTHING
           RETURNING id;""",
        (
            EVENT_TYPE,
            property_id,
            version,
            Jsonb({"property_id": str(property_id), "property_version": version}),
        ),
    )
    event = cursor.fetchone()
    if event is None:
        cursor.execute(
            """SELECT id FROM public.outbox_events
               WHERE event_type = %s AND aggregate_id = %s AND aggregate_version = %s;""",
            (EVENT_TYPE, property_id, version),
        )
        event = cursor.fetchone()
    cursor.execute(
        """INSERT INTO public.marketing_jobs
               (property_id, property_version, job_type, property_snapshot,
                source_event_id, created_by)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT ON CONSTRAINT marketing_jobs_once DO NOTHING
           RETURNING id;""",
        (property_id, version, JOB_TYPE, Jsonb(snapshot), event["id"], actor_id),
    )
    job = cursor.fetchone()
    if job is None:
        return None
    record_transition(cursor, job["id"], None, "queued", actor_id, reason)
    return job["id"]


def enqueue_on_publish(connection, property_id, actor_id):
    """Called inside the approval transaction. Never raises."""
    if not enabled():
        log.info("Marketing jobs disabled; property %s left for reconcile.", property_id)
        return None
    try:
        # Nested transaction == SAVEPOINT: a failure here leaves approval intact.
        with connection.transaction(), connection.cursor() as cursor:
            return enqueue(cursor, property_id, actor_id, "Property approved")
    except Exception as error:  # noqa: BLE001 - publication must not fail
        log.warning(
            "Marketing job not created for property %s (%s); reconcile will retry.",
            property_id,
            type(error).__name__,
        )
        return None


def reconcile(connection, actor_id=None) -> int:
    """Create jobs for approved, available listings that have none. Idempotent."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT p.id FROM public.properties AS p
               WHERE p.status = 'approved' AND p.availability = 'available'
                 AND NOT EXISTS (
                     SELECT 1 FROM public.marketing_jobs AS j
                     WHERE j.property_id = p.id AND j.job_type = %s
                       AND j.property_version = p.version)
               ORDER BY p.reviewed_at NULLS LAST, p.id;""",
            (JOB_TYPE,),
        )
        missing = [row["id"] for row in cursor.fetchall()]
    created = 0
    for property_id in missing:
        try:
            with connection.transaction(), connection.cursor() as cursor:
                if enqueue(cursor, property_id, actor_id, "Reconciled missing job"):
                    created += 1
        except Exception as error:  # noqa: BLE001 - one bad listing must not stop the rest
            log.warning(
                "Reconcile skipped property %s (%s).", property_id, type(error).__name__
            )
    return created


def transition(cursor, job_id, target, *, actor_id=None, reason=None, version=None):
    """Move a job to ``target`` if the transition table allows it."""
    cursor.execute(
        "SELECT id, status, version, last_error FROM public.marketing_jobs "
        "WHERE id = %s FOR UPDATE;",
        (job_id,),
    )
    job = cursor.fetchone()
    if job is None:
        raise JobNotFound()
    if version is not None and job["version"] != version:
        raise JobConflict("This marketing job changed. Refresh it and try again.")
    if not can_transition(job["status"], target):
        raise JobConflict(f"A {job['status']} job cannot move to {target}.")

    assignments = ["status = %s"]
    params = [target]
    if target == "failed":
        assignments.append("last_error = %s")
        params.append(clean_error(reason) or "Unknown failure")
    elif job["status"] == "failed" and target == "queued":
        # Manual retry: a fresh automatic-retry budget. The old error is kept
        # in the transition history.
        assignments += ["attempts = 0", "last_error = NULL", "next_attempt_at = clock_timestamp()"]
        reason = reason or f"Retry after: {job['last_error'] or 'unknown error'}"
    if target != "processing":
        assignments += ["locked_by = NULL", "locked_until = NULL"]
    cursor.execute(
        sql.SQL(
            "UPDATE public.marketing_jobs SET {}, version = version + 1, "
            "updated_at = clock_timestamp() WHERE id = %s;"
        ).format(sql.SQL(", ").join(sql.SQL(item) for item in assignments)),
        (*params, job_id),
    )
    record_transition(cursor, job_id, job["status"], target, actor_id, reason)


def mark_failed(cursor, job_id, error, actor_id=None):
    """For the render/publish workers: record a sanitised failure."""
    transition(cursor, job_id, "failed", actor_id=actor_id, reason=error)


# ---------------------------------------------------------------- HTTP API

router = APIRouter(prefix="/staff/marketing", tags=["Digital Studio"])

SUMMARY_COLUMNS = sql.SQL("""
    j.id, j.property_id, j.property_version,
    j.property_snapshot -> 'property' ->> 'title' AS property_title,
    j.job_type, j.status, j.attempts, j.max_attempts, j.last_error,
    j.created_at, j.updated_at, j.version""")


def scope(user):
    """Admins see every job; agents only jobs for listings they created."""
    if user["role"] == "admin":
        return sql.SQL("TRUE"), []
    return sql.SQL("p.created_by = %s"), [user["id"]]


def read_job(cursor, job_id, user):
    condition, params = scope(user)
    cursor.execute(
        sql.SQL("""SELECT {}, j.property_snapshot, j.source_event_id
                   FROM public.marketing_jobs AS j
                   JOIN public.properties AS p ON p.id = j.property_id
                   WHERE j.id = %s AND {};""").format(SUMMARY_COLUMNS, condition),
        (job_id, *params),
    )
    job = cursor.fetchone()
    if job is None:
        # Do not disclose whether another agent's job exists.
        raise HTTPException(status_code=404, detail="Marketing job not found.")
    cursor.execute(
        """SELECT from_status, to_status, actor_id, reason, created_at
           FROM public.marketing_job_transitions WHERE job_id = %s
           ORDER BY created_at, id;""",
        (job_id,),
    )
    return {**job, "transitions": cursor.fetchall()}


@router.get("/jobs", response_model=list[MarketingJobSummary])
def list_jobs(
    response: Response,
    status: JobStatus | None = None,
    property_id: UUID | None = None,
    before: UUID | None = Query(default=None, description="Job id from the previous page"),
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(require_staff),
):
    response.headers["Cache-Control"] = "no-store"
    condition, params = scope(user)
    conditions = [condition]
    if status is not None:
        conditions.append(sql.SQL("j.status = %s"))
        params.append(status)
    if property_id is not None:
        conditions.append(sql.SQL("j.property_id = %s"))
        params.append(property_id)
    if before is not None:
        conditions.append(sql.SQL(
            "(j.created_at, j.id) < (SELECT created_at, id FROM public.marketing_jobs WHERE id = %s)"
        ))
        params.append(before)
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("""SELECT {} FROM public.marketing_jobs AS j
                       JOIN public.properties AS p ON p.id = j.property_id
                       WHERE {} ORDER BY j.created_at DESC, j.id DESC LIMIT %s;""").format(
                SUMMARY_COLUMNS, sql.SQL(" AND ").join(conditions)
            ),
            (*params, limit),
        )
        return cursor.fetchall()


@router.get("/jobs/{job_id}", response_model=MarketingJobDetails)
def get_job(job_id: UUID, response: Response, user=Depends(require_staff)):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection, connection.cursor() as cursor:
        return read_job(cursor, job_id, user)


def change_job(job_id, payload, user, target, reason):
    with get_connection() as connection, connection.cursor() as cursor:
        try:
            transition(
                cursor, job_id, target,
                actor_id=user["id"], reason=reason, version=payload.version,
            )
        except JobNotFound:
            raise HTTPException(status_code=404, detail="Marketing job not found.")
        except JobConflict as conflict:
            raise HTTPException(status_code=409, detail=str(conflict))
        return read_job(cursor, job_id, user)


@router.post("/jobs/{job_id}/retry", response_model=MarketingJobDetails)
def retry_job(
    job_id: UUID, payload: JobVersion, response: Response, user=Depends(require_admin)
):
    response.headers["Cache-Control"] = "no-store"
    return change_job(job_id, payload, user, "queued", None)


@router.post("/jobs/{job_id}/cancel", response_model=MarketingJobDetails)
def cancel_job(
    job_id: UUID, payload: JobVersion, response: Response, user=Depends(require_admin)
):
    response.headers["Cache-Control"] = "no-store"
    return change_job(job_id, payload, user, "cancelled", "Cancelled by admin")


@router.post("/reconcile", response_model=ReconcileResult)
def reconcile_jobs(response: Response, user=Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        return {"created": reconcile(connection, user["id"])}


def main(argv):
    if argv[1:] != ["reconcile"]:
        print("Usage: python marketing_jobs.py reconcile", file=sys.stderr)
        return 2
    with get_connection() as connection:
        print(f"Created {reconcile(connection)} missing marketing job(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
