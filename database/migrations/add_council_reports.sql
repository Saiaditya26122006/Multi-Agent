-- Council Reports table — stores every council deliberation for transparency and document compiler
CREATE TABLE IF NOT EXISTS council_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    pipeline_run_id TEXT NOT NULL,
    section_number TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    score NUMERIC(4, 2) NOT NULL DEFAULT 0.0,
    decision TEXT NOT NULL CHECK (decision IN ('pass', 'revise', 'escalate')),
    critiques JSONB NOT NULL DEFAULT '[]'::jsonb,
    improvements_made JSONB NOT NULL DEFAULT '[]'::jsonb,
    revision_instructions TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_council_reports_session ON council_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_council_reports_pipeline ON council_reports(pipeline_run_id);
CREATE INDEX IF NOT EXISTS idx_council_reports_section ON council_reports(section_number);
