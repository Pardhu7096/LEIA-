-- ============================================================
-- LEIA DATABASE MIGRATION 011
-- GEO-M1: provenance of property coordinates
-- ============================================================
--
-- Additive only. Code from before this migration never reads these columns.
--
-- location_source       'gps'    one on-demand foreground fix from the Agent CRM
--                       'manual' typed or edited by the agent
--                       NULL     recorded by an older client (coordinates kept as-is)
-- location_accuracy_m   horizontal accuracy reported by the device (GPS only)
-- location_captured_at  device timestamp of the fix (GPS only)
--
-- No movement history: one row per property, overwritten on each save.
-- Staff-only data: excluded from public listing payloads and Digital Studio snapshots.
-- ============================================================

ALTER TABLE public.properties
    ADD COLUMN IF NOT EXISTS location_source TEXT,
    ADD COLUMN IF NOT EXISTS location_accuracy_m REAL,
    ADD COLUMN IF NOT EXISTS location_captured_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'properties_location_source_valid'
                     AND conrelid = 'public.properties'::regclass) THEN
        ALTER TABLE public.properties ADD CONSTRAINT properties_location_source_valid
            CHECK (location_source IS NULL OR location_source IN ('gps', 'manual'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'properties_location_accuracy_range'
                     AND conrelid = 'public.properties'::regclass) THEN
        ALTER TABLE public.properties ADD CONSTRAINT properties_location_accuracy_range
            CHECK (location_accuracy_m IS NULL OR location_accuracy_m BETWEEN 0 AND 5000);
    END IF;

    -- Location details only exist together with coordinates.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'properties_location_meta_needs_coordinates'
                     AND conrelid = 'public.properties'::regclass) THEN
        ALTER TABLE public.properties ADD CONSTRAINT properties_location_meta_needs_coordinates
            CHECK (latitude IS NOT NULL
                   OR (location_source IS NULL
                       AND location_accuracy_m IS NULL
                       AND location_captured_at IS NULL));
    END IF;

    -- GPS needs accuracy and time; manual and NULL sources carry neither.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'properties_location_meta_consistent'
                     AND conrelid = 'public.properties'::regclass) THEN
        ALTER TABLE public.properties ADD CONSTRAINT properties_location_meta_consistent
            CHECK (
                (location_source = 'gps'
                    AND location_accuracy_m IS NOT NULL
                    AND location_captured_at IS NOT NULL)
                OR ((location_source IS NULL OR location_source = 'manual')
                    AND location_accuracy_m IS NULL
                    AND location_captured_at IS NULL)
            );
    END IF;
END $$;

-- Before GEO-M1 the only way to enter coordinates was typing them.
UPDATE public.properties
SET location_source = 'manual'
WHERE latitude IS NOT NULL
  AND location_source IS NULL;

-- leia_app (if present) already holds SELECT, INSERT, UPDATE on public.properties;
-- table-level privileges cover the new columns.

-- ============================================================
-- END OF MIGRATION 011
-- ============================================================
