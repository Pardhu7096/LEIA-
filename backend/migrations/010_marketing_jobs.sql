-- Digital Studio M1: transactional outbox and marketing jobs.
-- Additive only. Approving a property records a property.published event and a
-- queued marketing job in the same transaction (under a savepoint), so a marketing
-- failure can never block publication. Unique keys make job creation idempotent.

CREATE TABLE IF NOT EXISTS public.outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL CHECK (event_type IN ('property.published')),
    aggregate_id UUID NOT NULL REFERENCES public.properties(id) ON DELETE CASCADE,
    aggregate_version INTEGER NOT NULL CHECK (aggregate_version >= 1),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    dispatched_at TIMESTAMPTZ,
    CONSTRAINT outbox_events_once UNIQUE (event_type, aggregate_id, aggregate_version)
);
CREATE INDEX IF NOT EXISTS outbox_events_pending_idx
    ON public.outbox_events (created_at) WHERE dispatched_at IS NULL;

CREATE TABLE IF NOT EXISTS public.marketing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES public.properties(id) ON DELETE CASCADE,
    property_version INTEGER NOT NULL CHECK (property_version >= 1),
    job_type TEXT NOT NULL DEFAULT 'reel_v1' CHECK (job_type IN ('reel_v1')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'processing', 'awaiting_review', 'approved',
        'publishing', 'published', 'failed', 'cancelled'
    )),
    -- Verified, non-private facts copied from the approved listing. Never edited.
    property_snapshot JSONB NOT NULL,
    source_event_id UUID REFERENCES public.outbox_events(id) ON DELETE SET NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    last_error TEXT CHECK (char_length(last_error) <= 500),
    -- Reserved for the M4 render worker (FOR UPDATE SKIP LOCKED leasing).
    next_attempt_at TIMESTAMPTZ,
    locked_by TEXT CHECK (char_length(locked_by) <= 100),
    locked_until TIMESTAMPTZ,
    created_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    CONSTRAINT marketing_jobs_once UNIQUE (property_id, job_type, property_version)
);
CREATE INDEX IF NOT EXISTS marketing_jobs_claim_idx
    ON public.marketing_jobs (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS marketing_jobs_property_idx
    ON public.marketing_jobs (property_id);
CREATE INDEX IF NOT EXISTS marketing_jobs_recent_idx
    ON public.marketing_jobs (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS public.marketing_job_transitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES public.marketing_jobs(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
    reason TEXT CHECK (char_length(reason) <= 500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS marketing_job_transitions_job_idx
    ON public.marketing_job_transitions (job_id, created_at);

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT, UPDATE ON public.outbox_events, public.marketing_jobs,
            public.marketing_job_transitions TO leia_app;
    END IF;
END $$;
