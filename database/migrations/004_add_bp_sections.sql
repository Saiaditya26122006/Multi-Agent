-- Build v2 — dedicated section-state table (production target for services/section_state.py)
-- Interim storage uses sessions.archived_state['bp_sections']; apply this to move
-- to a first-class table (better concurrency, querying, indexing), then switch
-- section_state._load/_save to this table (public API unchanged).

CREATE TABLE IF NOT EXISTS bp_sections (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL,
    section_id    TEXT NOT NULL,
    title         TEXT,
    agent         TEXT,
    status        TEXT NOT NULL DEFAULT 'not_started'
                  CHECK (status IN ('not_started','in_progress','blocked_on_data',
                                    'needs_review','done','failed')),
    draft         JSONB,
    blocked_on    TEXT,
    depends_on    TEXT[],
    version       INTEGER NOT NULL DEFAULT 0,
    last_updated  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, section_id)
);

CREATE INDEX IF NOT EXISTS idx_bp_sections_session ON bp_sections (session_id);
CREATE INDEX IF NOT EXISTS idx_bp_sections_status  ON bp_sections (session_id, status);
