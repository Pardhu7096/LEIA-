"""Digital Studio marketing job models and the allowed status transitions."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "queued",
    "processing",
    "awaiting_review",
    "approved",
    "publishing",
    "published",
    "failed",
    "cancelled",
]

# Every status change goes through this table. M1 exposes only retry
# (failed -> queued) and cancel; the rest are reserved for the render worker,
# human review and social publishing milestones.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"processing", "cancelled"}),
    "processing": frozenset({"awaiting_review", "failed", "queued"}),
    "awaiting_review": frozenset({"approved", "queued", "cancelled"}),
    "approved": frozenset({"publishing", "cancelled"}),
    "publishing": frozenset({"published", "failed"}),
    "failed": frozenset({"queued", "cancelled"}),
    "published": frozenset(),
    "cancelled": frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


class JobVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=2147483647)


class MarketingJobSummary(BaseModel):
    id: UUID
    property_id: UUID
    property_version: int
    property_title: str | None
    job_type: str
    status: JobStatus
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    version: int


class MarketingJobTransition(BaseModel):
    from_status: str | None
    to_status: str
    actor_id: UUID | None
    reason: str | None
    created_at: datetime


class MarketingJobDetails(MarketingJobSummary):
    property_snapshot: dict[str, Any]
    source_event_id: UUID | None
    transitions: list[MarketingJobTransition]


class ReconcileResult(BaseModel):
    created: int
