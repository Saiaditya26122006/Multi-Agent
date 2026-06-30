-- Migration: Add knowledge_base table for RAG system
-- Created: 2026-06-30
-- Requires: pgvector extension

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create knowledge_base table
CREATE TABLE IF NOT EXISTS knowledge_base (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    embedding vector(384) NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'ceo_doc',
        'conversation',
        'decision',
        'feedback',
        'correction',
        'agent_insight',
        'negative_knowledge',
        'preference_pattern',
        'external_research',
        'assumption_lifecycle',
        'contradiction_resolution',
        'run_metadata'
    )),
    section TEXT,
    epistemic_status TEXT CHECK (epistemic_status IN (
        'CONFIRMED',
        'ASSUMPTION',
        'CONTRADICTION',
        'INFERRED',
        'MISSING',
        'SUPERSEDED',
        'UNVERIFIED_EXTERNAL_CLAIM'
    )),
    topic_tags TEXT[] DEFAULT '{}',
    session_id TEXT,
    run_id TEXT,
    agent_name TEXT,
    confidence REAL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    superseded_by UUID REFERENCES knowledge_base(id),
    freshness_policy TEXT,
    last_confirmed TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- HNSW index for fast vector similarity search
CREATE INDEX IF NOT EXISTS knowledge_base_embedding_idx
    ON knowledge_base
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- B-tree indexes for metadata filtering
CREATE INDEX IF NOT EXISTS knowledge_base_source_type_idx
    ON knowledge_base (source_type);

CREATE INDEX IF NOT EXISTS knowledge_base_section_idx
    ON knowledge_base (section);

CREATE INDEX IF NOT EXISTS knowledge_base_epistemic_status_idx
    ON knowledge_base (epistemic_status);

CREATE INDEX IF NOT EXISTS knowledge_base_created_at_idx
    ON knowledge_base (created_at DESC);

CREATE INDEX IF NOT EXISTS knowledge_base_session_id_idx
    ON knowledge_base (session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS knowledge_base_agent_name_idx
    ON knowledge_base (agent_name)
    WHERE agent_name IS NOT NULL;

-- GIN index for topic_tags array search
CREATE INDEX IF NOT EXISTS knowledge_base_topic_tags_idx
    ON knowledge_base
    USING GIN (topic_tags);

-- Partial index for non-superseded records (most queries want active data)
CREATE INDEX IF NOT EXISTS knowledge_base_active_idx
    ON knowledge_base (created_at DESC)
    WHERE superseded_by IS NULL;

-- RLS policies
ALTER TABLE knowledge_base ENABLE ROW LEVEL SECURITY;

-- Service role can do everything
CREATE POLICY knowledge_base_service_all
    ON knowledge_base
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Comment for documentation
COMMENT ON TABLE knowledge_base IS 'RAG vector store for EpistemicOS multi-agent system. Stores embedded chunks from CEO data, conversations, decisions, and agent-generated knowledge.';
COMMENT ON COLUMN knowledge_base.source_type IS 'Origin category: ceo_doc, conversation, decision, feedback, correction, agent_insight, negative_knowledge, preference_pattern, external_research, assumption_lifecycle, contradiction_resolution, run_metadata';
COMMENT ON COLUMN knowledge_base.epistemic_status IS 'Truth status: CONFIRMED, ASSUMPTION, CONTRADICTION, INFERRED, MISSING, SUPERSEDED, UNVERIFIED_EXTERNAL_CLAIM';
COMMENT ON COLUMN knowledge_base.superseded_by IS 'Points to newer chunk that replaces this one. NULL = active/current.';
