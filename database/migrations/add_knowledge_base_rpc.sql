-- RPC function for vector similarity search on knowledge_base
-- Called by services/rag_service.py retrieve()

CREATE OR REPLACE FUNCTION match_knowledge_base(
    query_embedding vector(384),
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
