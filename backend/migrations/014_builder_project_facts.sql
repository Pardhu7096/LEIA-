-- LEIA pilot: builder, project, RERA, possession, floors and amenities, each with its source.
-- Additive only: every new column is nullable or has a constant default (metadata-only on
-- PostgreSQL 11+, no table rewrite). Existing listings keep every current value; new facts start as
-- 'unknown' / empty, and unknown never counts as a match in customer search.

CREATE TABLE public.builders (
    id UUID PRIMARY KEY,
    name VARCHAR(200) NOT NULL CHECK (btrim(name) <> ''),
    -- Duplicate protection: case, spacing and punctuation differences are the same builder.
    name_key TEXT GENERATED ALWAYS AS (regexp_replace(lower(name), '[^a-z0-9ఀ-౿]', '', 'g')) STORED,
    created_by UUID NOT NULL REFERENCES public.users (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT builders_name_key_not_empty CHECK (name_key <> ''),
    CONSTRAINT builders_name_key_unique UNIQUE (name_key)
);

ALTER TABLE public.properties
    ADD COLUMN builder_id UUID REFERENCES public.builders (id) ON DELETE RESTRICT,
    ADD COLUMN project_name VARCHAR(200),
    ADD COLUMN rera_number VARCHAR(50)
        CONSTRAINT properties_rera_number_format CHECK (rera_number ~ '^[A-Za-z0-9/_.-]{5,50}$'),
    -- Only an admin can set document_checked (enforced by the API); agents record owner_reported.
    ADD COLUMN rera_source TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT properties_rera_source_check
        CHECK (rera_source IN ('unknown', 'owner_reported', 'document_checked')),
    ADD COLUMN rera_checked_on DATE,
    ADD COLUMN rera_verified_by UUID REFERENCES public.users (id),
    ADD COLUMN construction_status TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT properties_construction_status_check
        CHECK (construction_status IN ('unknown', 'under_construction', 'ready_to_move', 'not_applicable')),
    ADD COLUMN possession_date DATE,
    ADD COLUMN floor_number SMALLINT CONSTRAINT properties_floor_number_check CHECK (floor_number BETWEEN -2 AND 200),
    ADD COLUMN total_floors SMALLINT CONSTRAINT properties_total_floors_check CHECK (total_floors BETWEEN 1 AND 200),
    ADD COLUMN lift TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT properties_lift_check CHECK (lift IN ('unknown', 'yes', 'no', 'not_applicable')),
    ADD COLUMN gated_community TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT properties_gated_community_check
        CHECK (gated_community IN ('unknown', 'yes', 'no', 'not_applicable')),
    ADD COLUMN security TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT properties_security_check
        CHECK (security IN ('unknown', 'none', 'guard', 'cctv', 'guard_and_cctv', 'not_applicable')),
    ADD COLUMN amenities TEXT[] NOT NULL DEFAULT '{}'
        CONSTRAINT properties_amenities_check
        CHECK (amenities <@ ARRAY['gym', 'pool', 'clubhouse', 'garden', 'play_area', 'community_hall']::text[]),
    ADD COLUMN project_notes TEXT CONSTRAINT properties_project_notes_check CHECK (length(project_notes) <= 2000),
    ADD COLUMN project_source TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT properties_project_source_check
        CHECK (project_source IN ('unknown', 'observed', 'owner_reported', 'document_checked')),
    ADD COLUMN project_checked_on DATE,
    ADD CONSTRAINT properties_floor_within_building
        CHECK (floor_number IS NULL OR total_floors IS NULL OR floor_number <= total_floors),
    ADD CONSTRAINT properties_possession_needs_status
        CHECK (possession_date IS NULL OR construction_status = 'under_construction'),
    ADD CONSTRAINT properties_rera_source_needs_number
        CHECK (rera_source = 'unknown' OR rera_number IS NOT NULL),
    ADD CONSTRAINT properties_rera_verified_by_needs_check
        CHECK (rera_verified_by IS NULL OR rera_source = 'document_checked');

CREATE INDEX properties_builder_idx ON public.properties (builder_id) WHERE builder_id IS NOT NULL;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT, UPDATE ON public.builders TO leia_app;
    END IF;
END $$;
