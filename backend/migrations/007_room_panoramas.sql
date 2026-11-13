-- Add independent 360-degree panoramas for named rooms without disturbing legacy single tours.
ALTER TABLE public.property_media DROP CONSTRAINT property_media_kind_check;
ALTER TABLE public.property_media ADD CONSTRAINT property_media_kind_check CHECK (
  (kind IN ('photo', 'panorama_room') AND room IS NOT NULL AND char_length(btrim(room)) BETWEEN 1 AND 60
    AND (kind <> 'panorama_room' OR NOT is_gallery_cover))
  OR (kind IN ('cover', 'panorama') AND room IS NULL AND NOT is_gallery_cover)
);
CREATE UNIQUE INDEX IF NOT EXISTS property_media_room_panorama_unique
  ON public.property_media(property_id, lower(room)) WHERE kind = 'panorama_room';
