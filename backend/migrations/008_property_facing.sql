ALTER TABLE public.properties
    ADD COLUMN facing text,
    ADD CONSTRAINT properties_facing_check CHECK (
        facing IN (
            'north', 'northeast', 'east', 'southeast',
            'south', 'southwest', 'west', 'northwest'
        )
    );