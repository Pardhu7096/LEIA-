-- Stand-in for the hand-made core tables of the development database
-- (PostgreSQL 18.6), as established by the 13 November 2026 comparison:
-- the CHECK constraints email_lowercase and session_expiry_valid, the three
-- secondary indexes, conversations.title TEXT, no default on conversations.id,
-- and chat_messages.property_id created before migration 005 existed.
-- Used only by tests to prove such a database is adopted without any change.
-- The authoritative check against the real database is schema_report.py --compare.
CREATE TABLE public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'agent', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT email_lowercase CHECK (email = lower(email))
);
CREATE TABLE public.auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT session_expiry_valid CHECK (expires_at > created_at)
);
CREATE INDEX auth_sessions_user_id_idx ON public.auth_sessions (user_id);
CREATE TABLE public.conversations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New property search',
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX conversations_user_updated_idx ON public.conversations (user_id, updated_at DESC);
-- Column order differs from a fresh build on purpose: order is not compared.
CREATE TABLE public.chat_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '[]'::jsonb,
    property_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX chat_messages_conversation_idx ON public.chat_messages (conversation_id, id);
