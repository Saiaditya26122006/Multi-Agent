-- Migration: Make claims first-class (Alex audit issue #3)
-- Created: 2026-07-18
-- Purpose: evidence_links.candidate_claim was free text, so the same claim was
-- duplicated across rows and sufficiency could not be aggregated per claim. This
-- promotes each atomic claim to a row keyed to its BP node, and links
-- evidence_links to it via claim_id. candidate_claim is kept for backfill/compat.
--
-- APPLY ONCE via the Supabase SQL Editor (DDL cannot be run from the app's
-- anon-key REST client). Safe/idempotent.

CREATE TABLE IF NOT EXISTS claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id TEXT NOT NULL,                 -- the BP node this claim belongs to
    claim_text TEXT NOT NULL,              -- the atomic claim
    claim_key TEXT,                        -- normalized text for dedup (lower/trimmed)
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
        'open',           -- stated, not yet assessed
        'supported',      -- has sufficient evidence
        'partial',        -- partially supported
        'unsupported',    -- no sufficient evidence
        'prohibited',     -- must not be claimed
        'retired'         -- withdrawn/superseded
    )),
    approved_version INT NOT NULL DEFAULT 0,   -- controller-approved version (0 = none)
    source_session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (node_id, claim_key)
);

CREATE INDEX IF NOT EXISTS idx_claims_node ON claims(node_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

-- Link evidence to the first-class claim (nullable; candidate_claim stays for compat).
ALTER TABLE evidence_links
    ADD COLUMN IF NOT EXISTS claim_id UUID REFERENCES claims(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_links_claim ON evidence_links(claim_id);

COMMENT ON TABLE claims IS 'First-class atomic claims per BP node (Alex audit #3). Evidence links point here via claim_id; sufficiency aggregates per claim.';
