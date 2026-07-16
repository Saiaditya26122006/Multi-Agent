-- Migration: Add evidence_links table for per-claim evidence boundaries
-- Created: 2026-07-16
-- Purpose: A fact may be RELEVANT to multiple nodes but only SUFFICIENT to
-- support specific claims. This table stores per-link boundaries so the
-- same fact can have different citation authority for different targets.

CREATE TABLE IF NOT EXISTS evidence_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
    target_node_id TEXT NOT NULL,
    claim_supported TEXT,
    claim_not_supported TEXT,
    sufficiency_status TEXT NOT NULL DEFAULT 'untested' CHECK (sufficiency_status IN (
        'sufficient',
        'partial',
        'insufficient',
        'untested',
        'blocked'
    )),
    boundary_reason TEXT,
    requires_corroboration BOOLEAN DEFAULT FALSE,
    controller_approved BOOLEAN DEFAULT FALSE,
    controller_decision_id UUID REFERENCES bp12_register(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_links_chunk ON evidence_links(chunk_id);
CREATE INDEX IF NOT EXISTS idx_evidence_links_target ON evidence_links(target_node_id);
CREATE INDEX IF NOT EXISTS idx_evidence_links_sufficiency ON evidence_links(sufficiency_status);

COMMENT ON TABLE evidence_links IS 'Per-claim evidence boundaries. The same fact can support one claim (sufficient) and not another (blocked) in different nodes.';
COMMENT ON COLUMN evidence_links.claim_supported IS 'What this evidence CAN support in the target node (specific claim text)';
COMMENT ON COLUMN evidence_links.claim_not_supported IS 'What this evidence CANNOT support despite being relevant (prevents misuse)';
COMMENT ON COLUMN evidence_links.sufficiency_status IS 'sufficient=proven, partial=contributes, insufficient=irrelevant, untested=not yet assessed, blocked=prohibited';
