CREATE TABLE public.property_media (
    id UUID PRIMARY KEY,
    property_id UUID NOT NULL REFERENCES public.properties (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('cover', 'panorama')),
    content BYTEA NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 12582912),
    UNIQUE (property_id, kind)
);

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.property_media TO leia_app;
    END IF;
END
$$;
