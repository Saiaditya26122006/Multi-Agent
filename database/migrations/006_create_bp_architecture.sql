-- Phase 0 — canonical BP architecture container table.
--
-- This is the STRUCTURE layer that Build reads: one row per architecture node,
-- the 24 governance columns from BP_architecture.xlsx 'Hoja 1' as real columns
-- (not JSONB), a unique node_id, and an embedding for node lookup.
--
-- It is NOT business content. Nothing here is a fact about the business, and
-- these rows must never be ingested into knowledge_base as facts.
--
-- Supersedes nothing yet. The 899 embedded architecture rows currently in
-- knowledge_base (source_type='ceo_doc', metadata.layer='bp_architecture') are
-- left in place and superseded in a later phase, once this table is proven.
--
-- Expected load: 912 rows, after four approved mappings —
--   dedup      64 duplicate node_ids renumbered into the .10+ band
--   reparent    7 nodes moved out of the phantom BP.9.7 branch
--   authoring   8 parent nodes created (24 children attached)
--   tier 2     10 placeholder purposes (contradiction resolved, still degraded)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS bp_architecture (
    -- ---- the 24 governance columns, in spreadsheet order -------------------
    node_id                              TEXT PRIMARY KEY,
    parent_node                          TEXT,
    level                                INTEGER CHECK (level BETWEEN 1 AND 6),
    node_type                            TEXT,
    atomic_status                        TEXT
        CHECK (atomic_status IN ('atomic', 'non_atomic')),
    node_title                           TEXT,
    purpose                              TEXT,
    required_output                      TEXT,
    output_format                        TEXT,
    proof_burden                         TEXT,
    evidence_requirement                 TEXT,
    evidence_gaps_assumptions            TEXT,
    linked_uncertainties                 TEXT,
    prohibited_claims_inference_patterns TEXT,
    dependencies                         TEXT,
    reopen_condition                     TEXT,
    decision_implication                 TEXT,
    execution_mode                       TEXT,
    human_review_type                    TEXT,
    executor                             TEXT,
    controller                           TEXT,
    architecture_status                  TEXT,
    evidence_status                      TEXT,
    notes_limitations                    TEXT,

    -- ---- provenance -------------------------------------------------------
    -- How this row's id or content came to be what it is. Single column by
    -- design; do not branch code on the .10+ id band, which is a reading aid.
    --   source      untouched row from the spreadsheet
    --   dedup       renumbered because its id collided (see renumbered_from)
    --   reparent    moved to a different parent (see renumbered_from)
    --   authored    parent node written by Alex; not in the spreadsheet
    --   placeholder purpose rewritten as a thin placeholder (Tier 2)
    --
    -- KNOWN LIMITATION: this column conflates node-origin (source/dedup/
    -- reparent/authored) with content-origin (placeholder). There is no live
    -- conflict — all 10 placeholder rows are unmoved first-instance nodes.
    -- TRIGGER TO SPLIT: the first node that is BOTH moved (dedup/reparent) AND
    -- carrying a placeholder purpose. At that point add purpose_provenance and
    -- leave this column to node-origin only.
    provenance      TEXT NOT NULL DEFAULT 'source'
        CHECK (provenance IN
               ('source', 'dedup', 'reparent', 'authored', 'placeholder')),
    renumbered_from TEXT,
    provenance_note TEXT,

    -- ---- classifier gating ------------------------------------------------
    -- degraded_target rows load and embed, but are excluded from the trusted
    -- classifier target set. A fact routed to one is held for review, never
    -- auto-filed. Clearing the flag requires repairing the underlying field.
    degraded_target BOOLEAN NOT NULL DEFAULT FALSE,
    degraded_reason TEXT
        CHECK (degraded_reason IN ('overwritten_purpose',
                                   'placeholder_purpose',
                                   'empty_shell',
                                   'null_purpose',
                                   'null_required_output')),

    embedding  vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- renumbered_from is meaningful only for a moved node
    CONSTRAINT bp_architecture_renumbered_from_requires_move
        CHECK (renumbered_from IS NULL
               OR provenance IN ('dedup', 'reparent')),

    -- a degraded row must say why, and a clean row must not claim a reason
    CONSTRAINT bp_architecture_degraded_reason_agrees
        CHECK ((degraded_target AND degraded_reason IS NOT NULL)
               OR (NOT degraded_target AND degraded_reason IS NULL)),

    -- the tree must stay connected. DEFERRABLE so one transaction can insert
    -- parents and children in any order; this makes the orphaned-parent class
    -- of defect that Phase 0 exists to fix structurally impossible to reload.
    CONSTRAINT bp_architecture_parent_fk
        FOREIGN KEY (parent_node) REFERENCES bp_architecture (node_id)
        DEFERRABLE INITIALLY DEFERRED
);

-- tree walk: Build resolves children by parent
CREATE INDEX IF NOT EXISTS bp_architecture_parent_node_idx
    ON bp_architecture (parent_node);

-- trusted-target lookup: the classifier's hot path is "not degraded"
CREATE INDEX IF NOT EXISTS bp_architecture_trusted_idx
    ON bp_architecture (node_id)
    WHERE NOT degraded_target;

-- node matching by embedding, same method as knowledge_base
CREATE INDEX IF NOT EXISTS bp_architecture_embedding_idx
    ON bp_architecture
    USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE bp_architecture IS
    'Canonical BP architecture structure (Phase 0). Container layer, not business '
    'content — never ingest these rows into knowledge_base as facts.';
COMMENT ON COLUMN bp_architecture.degraded_target IS
    'Excluded from the trusted classifier target set; facts routing here are held '
    'for review. See PROJECT_STATE.md, Phase 0 section.';
COMMENT ON COLUMN bp_architecture.renumbered_from IS
    'Prior node_id. Source of truth for what moved — the .10+ suffix band is a '
    'reading aid only and must not be branched on.';
