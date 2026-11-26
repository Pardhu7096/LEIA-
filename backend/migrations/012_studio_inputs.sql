ALTER TABLE public.users
    ADD COLUMN studio_phone TEXT,
    ADD COLUMN studio_phone_verified_at TIMESTAMPTZ,
    ADD COLUMN studio_phone_verified_by UUID REFERENCES public.users(id),
    ADD CONSTRAINT users_studio_phone_verified CHECK (
        (studio_phone IS NULL AND studio_phone_verified_at IS NULL AND studio_phone_verified_by IS NULL)
        OR (studio_phone IS NOT NULL AND studio_phone ~ '^[+][1-9][0-9]{7,14}$'
            AND studio_phone_verified_at IS NOT NULL AND studio_phone_verified_by IS NOT NULL)
    );

CREATE TABLE public.studio_walkthroughs (
    id UUID PRIMARY KEY,
    property_id UUID NOT NULL UNIQUE REFERENCES public.properties(id) ON DELETE CASCADE,
    uploaded_by UUID NOT NULL REFERENCES public.users(id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    bytes INTEGER NOT NULL CHECK (bytes BETWEEN 1 AND 104857600)
);
