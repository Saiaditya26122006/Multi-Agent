-- Migration: Upgrade embedding dimension from 384 (MiniLM) to 1024 (Cohere Embed v3)
-- Created: 2026-07-10
-- IMPORTANT: Run this BEFORE re-embedding. Existing 384-dim vectors will be
-- incompatible — the re-embed script handles the full re-indexing.

-- Step 1: Drop the old HNSW index (it's dimension-locked)
DROP INDEX IF EXISTS knowledge_base_embedding_idx;

-- Step 2: Alter the embedding column to 1024 dimensions
ALTER TABLE knowledge_base
    ALTER COLUMN embedding TYPE vector(1024);

-- Step 3: Recreate HNSW index for 1024-dim vectors
-- Using higher ef_construction for better recall at 1024 dims
CREATE INDEX knowledge_base_embedding_idx
    ON knowledge_base
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 100);

-- Step 4: Replace the RPC function to accept 1024-dim input
CREATE OR REPLACE FUNCTION match_knowledge_base(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.4,
    match_count int DEFAULT 15
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    source_type TEXT,
    section TEXT,
    epistemic_status TEXT,
    topic_tags TEXT[],
    session_id TEXT,
    run_id TEXT,
    agent_name TEXT,
    confidence REAL,
    superseded_by UUID,
    freshness_policy TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        kb.id,
        kb.content,
        kb.source_type,
        kb.section,
        kb.epistemic_status,
        kb.topic_tags,
        kb.session_id,
        kb.run_id,
        kb.agent_name,
        kb.confidence,
        kb.superseded_by,
        kb.freshness_policy,
        kb.metadata,
        kb.created_at,
        1 - (kb.embedding <=> query_embedding) AS similarity
    FROM knowledge_base kb
    WHERE 1 - (kb.embedding <=> query_embedding) > match_threshold
    ORDER BY kb.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
