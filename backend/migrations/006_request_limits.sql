CREATE TABLE public.request_limits (
    key TEXT PRIMARY KEY,
    count INTEGER NOT NULL CHECK (count > 0),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX request_limits_expiry ON public.request_limits (expires_at);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.request_limits TO leia_app;
    END IF;
END $$;
