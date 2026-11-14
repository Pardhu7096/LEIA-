-- Baseline for the account, session and chat tables that were created manually
-- during initial development and that every later migration and the API assume.
-- Without it, an empty database (new staging/production server) cannot be built
-- from this repository: 001 fails on the missing public.users table.
--
-- The definitions reproduce the development database (PostgreSQL 18.6) as compared
-- on 13 November 2026, including its CHECK constraints, indexes, column types and
-- defaults. `python schema_report.py --compare tests/expected_schema.json` checks
-- any database against the schema these migrations build.
--
-- Every statement is IF NOT EXISTS, so installations that already have these
-- tables (the development database) are adopted unchanged: nothing is altered,
-- dropped or rewritten there. chat_messages.property_id is left to 005, which
-- adds it only where it is missing, so both kinds of database converge.
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'agent', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- The API lower-cases addresses before every insert and lookup.
    CONSTRAINT email_lowercase CHECK (email = lower(email))
);

CREATE TABLE IF NOT EXISTS public.auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT session_expiry_valid CHECK (expires_at > created_at)
);

-- No id default: the API always supplies the conversation id (uuid4).
CREATE TABLE IF NOT EXISTS public.conversations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New property search',
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- property_id is added by 005 so existing installations and new ones converge.
CREATE TABLE IF NOT EXISTS public.chat_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The indexes behind session lookups by account, message history in order and
-- the saved-chat list (newest first). Same names as in development.
CREATE INDEX IF NOT EXISTS auth_sessions_user_id_idx
    ON public.auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS chat_messages_conversation_idx
    ON public.chat_messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
    ON public.conversations (user_id, updated_at DESC);

-- Least privilege for the API role used by existing installations.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT ON public.users TO leia_app;
        GRANT SELECT, INSERT, UPDATE ON public.auth_sessions TO leia_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.conversations TO leia_app;
        GRANT SELECT, INSERT, DELETE ON public.chat_messages TO leia_app;
    END IF;
END $$;
