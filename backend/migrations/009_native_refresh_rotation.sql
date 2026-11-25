-- ============================================================
-- LEIA DATABASE MIGRATION 009
-- Native (Android) refresh-token rotation and reuse detection
-- ============================================================
--
-- Additive only. Existing cookie sessions (customer web and Agent CRM) keep
-- using refresh_token_hash exactly as before; the new columns stay NULL
-- for them.
--
-- previous_refresh_token_hash  hash of the token replaced by the last rotation
-- rotation_request_hash        hash of the client request_id of that rotation;
--                              a retry with the same id is answered idempotently,
--                              any other reuse revokes the session
-- rotated_at                   time of the last rotation (bounds the retry window)
-- revoke_reason                logout | reuse_detected (NULL for older rows)
-- ============================================================

ALTER TABLE public.auth_sessions
    ADD COLUMN IF NOT EXISTS previous_refresh_token_hash TEXT,
    ADD COLUMN IF NOT EXISTS rotation_request_hash TEXT,
    ADD COLUMN IF NOT EXISTS rotated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoke_reason TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'auth_sessions_revoke_reason_valid'
          AND conrelid = 'public.auth_sessions'::regclass
    ) THEN
        ALTER TABLE public.auth_sessions
            ADD CONSTRAINT auth_sessions_revoke_reason_valid
            CHECK (revoke_reason IS NULL
                   OR revoke_reason IN ('logout', 'reuse_detected'));
    END IF;
END $$;

-- Lookup of a presented token that was already rotated away.
CREATE UNIQUE INDEX IF NOT EXISTS auth_sessions_previous_token_hash_idx
    ON public.auth_sessions (previous_refresh_token_hash)
    WHERE previous_refresh_token_hash IS NOT NULL;

-- leia_app already holds SELECT, INSERT, UPDATE on auth_sessions (migration 000);
-- table-level privileges cover the new columns.

-- ============================================================
-- END OF MIGRATION 009
-- ============================================================
