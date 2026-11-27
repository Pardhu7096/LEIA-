-- LEIA AI cost ledger: hard daily/monthly caps enforced by the application.
-- Rows: 'day:YYYY-MM-DD' and 'month:YYYY-MM' (UTC). Costs in micro-rupees.
-- Additive only: safe to apply while the current release is live.
CREATE TABLE public.ai_usage_periods (
    period TEXT PRIMARY KEY CHECK (period ~ '^(day:\d{4}-\d{2}-\d{2}|month:\d{4}-\d{2})$'),
    requests INTEGER NOT NULL DEFAULT 0 CHECK (requests >= 0),
    input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    spent_micro_inr BIGINT NOT NULL DEFAULT 0 CHECK (spent_micro_inr >= 0),
    reserved_micro_inr BIGINT NOT NULL DEFAULT 0 CHECK (reserved_micro_inr >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'leia_app') THEN
        GRANT SELECT, INSERT, UPDATE ON public.ai_usage_periods TO leia_app;
    END IF;
END $$;
