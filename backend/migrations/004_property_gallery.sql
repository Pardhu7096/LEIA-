ALTER TABLE public.property_media
    DROP CONSTRAINT property_media_property_id_kind_key,
    DROP CONSTRAINT property_media_kind_check,
    ADD COLUMN room TEXT,
    ADD COLUMN is_gallery_cover BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN uploaded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    ADD CONSTRAINT property_media_kind_check CHECK (
        (kind = 'photo' AND room IS NOT NULL AND char_length(btrim(room)) BETWEEN 1 AND 60)
        OR (kind IN ('cover', 'panorama') AND room IS NULL AND NOT is_gallery_cover)
    );

CREATE UNIQUE INDEX property_media_single_slot
    ON public.property_media (property_id, kind) WHERE kind IN ('cover', 'panorama');
CREATE UNIQUE INDEX property_media_gallery_cover
    ON public.property_media (property_id) WHERE is_gallery_cover;
CREATE INDEX property_media_gallery
    ON public.property_media (property_id, uploaded_at, id) WHERE kind = 'photo';
