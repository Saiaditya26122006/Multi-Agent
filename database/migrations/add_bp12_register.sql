-- Migration: Add bp12_register table for unresolved governance items
-- Created: 2026-07-16
-- Purpose: BP.12 is the risk/uncertainty register. All contradictions,
-- evidence gaps, unresolved assumptions, and prohibited inferences are
-- linked here until a controller reviews and resolves them.

CREATE TABLE IF NOT EXISTS bp12_register (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_type TEXT NOT NULL CHECK (item_type IN (
        'contradiction',
        'evidence_gap',
        'unresolved_assumption',
        'prohibited_inference',
        'source_conflict',
        'sufficiency_failure'
    )),
    title TEXT NOT NULL,
    description TEXT,
    affected_chunk_ids UUID[] DEFAULT '{}',
    affected_node_ids TEXT[] DEFAULT '{}',
    affected_assumption_ids UUID[] DEFAULT '{}',
    created_task_ids UUID[] DEFAULT '{}',
    severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN (
        'critical', 'high', 'medium', 'low'
    )),
    resolution_status TEXT NOT NULL DEFAULT 'open' CHECK (resolution_status IN (
        'open',
        'under_review',
        'resolved',
        'accepted_risk',
        'escalated'
    )),
    controller_decision TEXT,
    controller_decided_at TIMESTAMPTZ,
    controller_reasoning TEXT,
    source_session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bp12_register_type ON bp12_register(item_type);
CREATE INDEX IF NOT EXISTS idx_bp12_register_status ON bp12_register(resolution_status);
CREATE INDEX IF NOT EXISTS idx_bp12_register_severity ON bp12_register(severity);
CREATE INDEX IF NOT EXISTS idx_bp12_register_open ON bp12_register(created_at DESC) WHERE resolution_status = 'open';

COMMENT ON TABLE bp12_register IS 'BP.12 governance register. All unresolved contradictions, evidence gaps, and prohibited inferences are tracked here until controller review.';
COMMENT ON COLUMN bp12_register.affected_chunk_ids IS 'Knowledge base chunks involved in this issue';
COMMENT ON COLUMN bp12_register.affected_assumption_ids IS 'Assumption chunks that may be invalidated by this issue';
COMMENT ON COLUMN bp12_register.created_task_ids IS 'Tasks created to investigate/resolve this issue';
COMMENT ON COLUMN bp12_register.controller_decision IS 'Final decision: resolve, accept_risk, kill, reclassify, etc.';
