CREATE TABLE IF NOT EXISTS runs (
    id text PRIMARY KEY,
    mode text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cameras (
    run_id text NOT NULL REFERENCES runs(id),
    id text NOT NULL,
    name text NOT NULL,
    area text NOT NULL,
    road boolean NOT NULL,
    coordinates jsonb,
    status text NOT NULL DEFAULT 'waiting',
    last_poll timestamptz,
    last_frame timestamptz,
    last_digest text,
    last_source_at timestamptz,
    PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS frames (
    id uuid PRIMARY KEY,
    run_id text NOT NULL,
    camera_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    source_at timestamptz,
    digest text NOT NULL,
    media_key text NOT NULL,
    width integer NOT NULL,
    height integer NOT NULL,
    FOREIGN KEY (run_id, camera_id) REFERENCES cameras (run_id, id)
);
CREATE INDEX IF NOT EXISTS frames_recent ON frames (run_id, camera_id, observed_at DESC);
CREATE TABLE IF NOT EXISTS jobs (
    frame_id uuid PRIMARY KEY REFERENCES frames(id) ON DELETE CASCADE,
    stage text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_id uuid,
    error text
);
CREATE INDEX IF NOT EXISTS jobs_available ON jobs (stage, available_at);
CREATE TABLE IF NOT EXISTS observations (
    frame_id uuid PRIMARY KEY REFERENCES frames(id) ON DELETE CASCADE,
    scene jsonb NOT NULL,
    analyzed_at timestamptz NOT NULL DEFAULT now(),
    duration_ms integer
);
CREATE TABLE IF NOT EXISTS events (
    id uuid PRIMARY KEY,
    run_id text NOT NULL,
    camera_id text NOT NULL,
    kind text NOT NULL,
    severity text NOT NULL,
    reason text NOT NULL,
    first_frame uuid REFERENCES frames(id),
    latest_frame uuid REFERENCES frames(id),
    opened_at timestamptz NOT NULL,
    last_seen timestamptz NOT NULL,
    ended_at timestamptz,
    state text NOT NULL DEFAULT 'new',
    version integer NOT NULL DEFAULT 1,
    FOREIGN KEY (run_id, camera_id) REFERENCES cameras (run_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS events_active ON events (run_id, camera_id, kind) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS actions (
    event_id uuid NOT NULL REFERENCES events(id),
    request_id uuid NOT NULL,
    actor text NOT NULL,
    state text NOT NULL,
    note text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, request_id)
);
CREATE TABLE IF NOT EXISTS forecasts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL REFERENCES runs(id),
    point text NOT NULL,
    fetched_at timestamptz NOT NULL,
    periods jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS forecasts_recent ON forecasts (run_id, point, id DESC);
CREATE TABLE IF NOT EXISTS warnings (
    run_id text NOT NULL REFERENCES runs(id),
    point text NOT NULL,
    payload jsonb,
    revision integer NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, point)
);
CREATE TABLE IF NOT EXISTS summaries (
    run_id text NOT NULL REFERENCES runs(id),
    day date NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, day)
);
CREATE TABLE IF NOT EXISTS outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL REFERENCES runs(id),
    key text NOT NULL,
    kind text NOT NULL,
    recipient text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    media_key text,
    stage text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_id uuid,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, key, recipient)
);
CREATE INDEX IF NOT EXISTS outbox_available ON outbox (stage, available_at);
CREATE TABLE IF NOT EXISTS checks (
    run_id text NOT NULL REFERENCES runs(id),
    name text NOT NULL,
    status text NOT NULL,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, name)
);
