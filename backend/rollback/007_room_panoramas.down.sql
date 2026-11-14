-- Reverse 007_room_panoramas.sql: back to the exact schema after 006.
-- Run as the database owner, with the backend stopped:
--   psql -v ON_ERROR_STOP=1 -h <host> -p <port> -U postgres -d leia -f 007_room_panoramas.down.sql
-- One transaction. It refuses (and changes nothing) unless 007 is recorded and no
-- room panoramas exist: the old rule cannot hold them, and deleting customer media
-- is a separate decision. Not a tracked migration (kept outside migrations/).
BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    room_panoramas bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.crm_schema_migrations
                   WHERE name = '007_room_panoramas.sql') THEN
        RAISE EXCEPTION 'Rollback refused: 007_room_panoramas.sql is not recorded.';
    END IF;
    SELECT count(*) INTO room_panoramas FROM public.property_media WHERE kind = 'panorama_room';
    IF room_panoramas > 0 THEN
        RAISE EXCEPTION 'Rollback refused: % room panorama(s) exist. Back them up and decide on them first.',
            room_panoramas;
    END IF;
END $$;

DROP INDEX public.property_media_room_panorama_unique;
ALTER TABLE public.property_media DROP CONSTRAINT property_media_kind_check;
-- The rule exactly as 004_property_gallery.sql created it.
ALTER TABLE public.property_media ADD CONSTRAINT property_media_kind_check CHECK (
    (kind = 'photo' AND room IS NOT NULL AND char_length(btrim(room)) BETWEEN 1 AND 60)
    OR (kind IN ('cover', 'panorama') AND room IS NULL AND NOT is_gallery_cover)
);
DELETE FROM public.crm_schema_migrations WHERE name = '007_room_panoramas.sql';

COMMIT;
