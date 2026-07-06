-- chunk_relationships: connects facts in the knowledge base
-- Stores semantic links (confirms, contradicts, updates, depends_on, related)

CREATE TABLE IF NOT EXISTS chunk_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id_a UUID NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
    chunk_id_b UUID NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('confirms', 'contradicts', 'updates', 'depends_on', 'related')),
    confidence FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id TEXT,
    CONSTRAINT no_self_reference CHECK (chunk_id_a != chunk_id_b),
    CONSTRAINT unique_relationship UNIQUE (chunk_id_a, chunk_id_b, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_chunk_relationships_a ON chunk_relationships(chunk_id_a);
CREATE INDEX IF NOT EXISTS idx_chunk_relationships_b ON chunk_relationships(chunk_id_b);
CREATE INDEX IF NOT EXISTS idx_chunk_relationships_type ON chunk_relationships(relationship_type);
