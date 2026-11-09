-- Older messages remain part of the general conversation.
ALTER TABLE IF EXISTS public.chat_messages
    ADD COLUMN IF NOT EXISTS property_id UUID;
