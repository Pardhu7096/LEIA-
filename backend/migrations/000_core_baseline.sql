
-- ============================================================
-- LEIA DATABASE MIGRATION 000
-- Core baseline: users, auth sessions, conversations and chat
-- ============================================================
--
-- Purpose:
-- Initialize the core LEIA tables before CRM migrations 001-007.
--
-- Existing tables are not dropped or recreated.
-- Missing constraints and indexes are added conditionally.
--
-- IMPORTANT:
-- Test on an isolated database before applying to staging.
-- ============================================================

-- 1. USERS
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer'
        CHECK (role IN ('customer', 'agent', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT email_lowercase
        CHECK (email = lower(email))
);

-- 2. AUTHENTICATION SESSIONS
CREATE TABLE IF NOT EXISTS public.auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL
        REFERENCES public.users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT session_expiry_valid
        CHECK (expires_at > created_at)
);

-- 3. CONVERSATIONS
CREATE TABLE IF NOT EXISTS public.conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL
        REFERENCES public.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New property search',
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. CHAT MESSAGES
-- Migration 005 adds property_id.
CREATE TABLE IF NOT EXISTS public.chat_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id UUID NOT NULL
        REFERENCES public.conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL
        CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5. ENSURE REQUIRED CONSTRAINTS EXIST
-- For databases where the tables were created previously.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'email_lowercase'
          AND conrelid = 'public.users'::regclass
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT email_lowercase
            CHECK (email = lower(email));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'session_expiry_valid'
          AND conrelid = 'public.auth_sessions'::regclass
    ) THEN
        ALTER TABLE public.auth_sessions
            ADD CONSTRAINT session_expiry_valid
            CHECK (expires_at > created_at);
    END IF;
END $$;

-- 6. PERFORMANCE INDEXES

CREATE INDEX IF NOT EXISTS auth_sessions_user_id_idx
    ON public.auth_sessions (user_id);

CREATE INDEX IF NOT EXISTS chat_messages_conversation_idx
    ON public.chat_messages (conversation_id, id);

CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
    ON public.conversations (user_id, updated_at DESC);

-- 7. LEGACY API ROLE PERMISSIONS
-- Apply grants only if the local leia_app role exists.
-- Render staging permissions will be verified separately.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'leia_app'
    ) THEN
        GRANT SELECT, INSERT
            ON public.users TO leia_app;

        GRANT SELECT, INSERT, UPDATE
            ON public.auth_sessions TO leia_app;

        GRANT SELECT, INSERT, UPDATE, DELETE
            ON public.conversations TO leia_app;

        GRANT SELECT, INSERT, DELETE
            ON public.chat_messages TO leia_app;
    END IF;
END $$;

-- ============================================================
-- END OF MIGRATION 000
-- ============================================================
