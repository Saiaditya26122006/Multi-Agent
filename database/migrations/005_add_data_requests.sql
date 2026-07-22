-- Build v2 Phase 2 — data-request handshake.
-- A section-agent that hits a gap it can't fill emits a structured request here.
-- When Alex feeds data that classifies to a target node, the request auto-closes
-- and the section unblocks (see services/data_requests.py).

CREATE TABLE IF NOT EXISTS data_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL,
    section_id      TEXT NOT NULL,
    agent           TEXT,
    target_nodes    TEXT[],            -- BP node ids that would satisfy this need
    description     TEXT NOT NULL,     -- what Alex should provide
    why             TEXT,              -- why the agent needs it
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','fulfilled','cancelled')),
    fulfilled_by_chunk UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fulfilled_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_data_requests_open
    ON data_requests (session_id, status);
