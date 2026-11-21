ALTER TABLE cameras ADD COLUMN location_note text;
ALTER TABLE cameras ADD COLUMN interval_seconds integer CHECK (interval_seconds > 0);
