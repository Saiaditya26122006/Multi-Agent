-- Memory Profile Table
-- Stores long-term memory extracted from completed sessions

CREATE TABLE IF NOT EXISTS memory_profile (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ceo_id UUID REFERENCES ceo_context(id) ON DELETE CASCADE,
    memory_type TEXT NOT NULL CHECK (memory_type IN (
        'strategic_decision',
        'recurring_priority',
        'validated_assumption',
        'key_contact',
        'market_insight',
        'communication_pattern'
    )),
    content TEXT NOT NULL,
    source_session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
    created_at TIMESTAMPTZ DEFAULT now(),
    last_referenced_at TIMESTAMPTZ DEFAULT now(),

    -- Add indexes for better query performance
    CONSTRAINT memory_profile_content_check CHECK (length(content) > 0)
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_memory_profile_ceo_id ON memory_profile(ceo_id);
CREATE INDEX IF NOT EXISTS idx_memory_profile_type ON memory_profile(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_profile_created_at ON memory_profile(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_profile_last_referenced ON memory_profile(last_referenced_at DESC);

-- Add comments for documentation
COMMENT ON TABLE memory_profile IS 'Stores long-term memory extracted from completed sessions';
COMMENT ON COLUMN memory_profile.memory_type IS 'Type of memory: strategic_decision, recurring_priority, validated_assumption, key_contact, market_insight, communication_pattern';
COMMENT ON COLUMN memory_profile.confidence IS 'Confidence level of this memory: low, medium, high';
COMMENT ON COLUMN memory_profile.last_referenced_at IS 'Last time this memory was accessed or mentioned';
