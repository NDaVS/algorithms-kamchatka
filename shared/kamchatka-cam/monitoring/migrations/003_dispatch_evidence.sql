ALTER TABLE cameras ADD COLUMN road_priority smallint NOT NULL DEFAULT 0 CHECK (road_priority BETWEEN 0 AND 3);
ALTER TABLE cameras ADD COLUMN road_priority_reason text;
ALTER TABLE events ADD COLUMN after_work_frame uuid REFERENCES frames(id);
ALTER TABLE events ADD COLUMN after_work_actor text;
ALTER TABLE events ADD COLUMN after_work_at timestamptz;

CREATE TABLE signal_feedback (
    event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    actor text NOT NULL,
    verdict text NOT NULL CHECK (verdict IN ('confirmed','false_alarm','already_working')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, actor)
);
