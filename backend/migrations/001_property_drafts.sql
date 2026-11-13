-- Baseline for the CRM tables created manually during initial development.
-- Requires the existing LEIA public.users table. Never drops existing records.
CREATE TABLE IF NOT EXISTS public.properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_by UUID NOT NULL REFERENCES public.users(id),
    title VARCHAR(200) NOT NULL,
    description TEXT,
    property_type TEXT NOT NULL CHECK (property_type IN ('flat', 'independent_house', 'plot')),
    price BIGINT CHECK (price > 0),
    bedrooms SMALLINT CHECK (bedrooms BETWEEN 0 AND 20),
    bathrooms SMALLINT CHECK (bathrooms BETWEEN 0 AND 20),
    area_sqft NUMERIC(12, 2) CHECK (area_sqft > 0),
    address TEXT,
    locality VARCHAR(150),
    city VARCHAR(100),
    pincode VARCHAR(6) CHECK (pincode ~ '^[0-9]{6}$'),
    latitude DOUBLE PRECISION CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION CHECK (longitude BETWEEN -180 AND 180),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'approved', 'rejected', 'archived')),
    availability TEXT NOT NULL DEFAULT 'available'
        CHECK (availability IN ('available', 'sold', 'withdrawn')),
    submitted_at TIMESTAMPTZ,
    reviewed_by UUID REFERENCES public.users(id),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT property_coordinates_together CHECK (
        (latitude IS NULL AND longitude IS NULL)
        OR (latitude IS NOT NULL AND longitude IS NOT NULL)
    )
);
CREATE TABLE IF NOT EXISTS public.property_private_details (
    property_id UUID PRIMARY KEY REFERENCES public.properties(id) ON DELETE CASCADE,
    owner_name VARCHAR(150),
    owner_phone VARCHAR(30),
    owner_email VARCHAR(254),
    listing_consent BOOLEAN NOT NULL DEFAULT FALSE,
    consent_recorded_at TIMESTAMPTZ,
    agent_notes TEXT,
    review_notes TEXT
);
CREATE INDEX IF NOT EXISTS properties_created_by_idx ON public.properties(created_by);
CREATE INDEX IF NOT EXISTS properties_status_availability_idx ON public.properties(status, availability);

-- Existing local installations use leia_app for the API.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT, UPDATE ON public.properties, public.property_private_details TO leia_app;
    END IF;
END $$;
