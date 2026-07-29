-- RPC for vector similarity search on bp_architecture.
-- Mirrors match_knowledge_base (add_knowledge_base_rpc.sql).
--
-- Needed because PostgREST cannot express `ORDER BY embedding <=> $1`, so without
-- this the only way to search is to pull all 904 vectors to the client and rank
-- them there — correct, but it transfers ~11 MB per query and never touches the
-- HNSW index.
--
-- `<=>` is cosine DISTANCE; similarity is 1 - distance. The ORDER BY must stay on
-- the raw `<=>` expression for the HNSW index to be used — ordering by the derived
-- similarity column instead forces a sequential scan.
--
-- trusted_only DEFAULTS TO FALSE, deliberately. Filtering degraded rows out of the
-- SEARCH does not protect a fact — it redirects it to a wrong node that looks fine.
-- Measured on this data:
--
--   "what are our gross margin assumptions"
--     trusted_only=FALSE -> BP.9.5.10 Gross Margin Assumptions        0.8700
--     trusted_only=TRUE  -> BP.9.3.5  GTM Assumptions                 0.4519
--
--   "how do we sequence hiring"
--     trusted_only=FALSE -> BP.9.5.17 Hiring Sequence                 0.8118
--     trusted_only=TRUE  -> BP.6.6.9  Adoption Sequencing Acceptance  0.3574
--
-- Both substitutes clear the 0.3 threshold, so they would be auto-filed with
-- apparent confidence — a hiring fact landing in customer adoption. The degraded
-- gate belongs at the FILING decision, not the query: retrieve everything, check
-- degraded_target on the top match, and hold that fact for review.
--
-- Pass trusted_only=TRUE only when you want the trusted subset for its own sake
-- (e.g. reporting which nodes are safe targets), never as a classifier guard.

CREATE OR REPLACE FUNCTION match_bp_architecture(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.3,
    match_count int DEFAULT 10,
    trusted_only boolean DEFAULT FALSE
)
RETURNS TABLE (
    node_id         TEXT,
    node_title      TEXT,
    parent_node     TEXT,
    level           INTEGER,
    atomic_status   TEXT,
    purpose         TEXT,
    required_output TEXT,
    provenance      TEXT,
    renumbered_from TEXT,
    degraded_target BOOLEAN,
    degraded_reason TEXT,
    similarity      float
)
LANGUAGE sql STABLE
AS $$
    SELECT
        a.node_id,
        a.node_title,
        a.parent_node,
        a.level,
        a.atomic_status,
        a.purpose,
        a.required_output,
        a.provenance,
        a.renumbered_from,
        a.degraded_target,
        a.degraded_reason,
        1 - (a.embedding <=> query_embedding) AS similarity
    FROM bp_architecture a
    WHERE a.embedding IS NOT NULL
      AND (NOT trusted_only OR NOT a.degraded_target)
      AND 1 - (a.embedding <=> query_embedding) >= match_threshold
    ORDER BY a.embedding <=> query_embedding
    LIMIT match_count;
$$;

COMMENT ON FUNCTION match_bp_architecture IS
    'Nearest architecture nodes by cosine similarity. trusted_only excludes '
    'degraded_target rows. Threshold 0.3 matches DEFAULT_THRESHOLD in '
    'services/rag_service.py — Titan v2 similarities are compressed, so anything '
    'above ~0.5 silently matches nothing.';

-- ---------------------------------------------------------------------------
-- Index-usage check. Run this in the SQL editor and confirm the plan contains
-- "Index Scan using bp_architecture_embedding_idx", NOT "Seq Scan".
--
--   EXPLAIN ANALYZE
--   SELECT node_id, 1 - (embedding <=> (
--       SELECT embedding FROM bp_architecture WHERE node_id = 'BP.5.1.2'
--   )) AS similarity
--   FROM bp_architecture
--   WHERE embedding IS NOT NULL
--   ORDER BY embedding <=> (
--       SELECT embedding FROM bp_architecture WHERE node_id = 'BP.5.1.2'
--   )
--   LIMIT 10;
--
-- Note: with only 904 rows Postgres may legitimately prefer a sequential scan —
-- at this size it is genuinely cheaper. To force the index for verification:
--   SET enable_seqscan = OFF;  -- then re-run, then RESET enable_seqscan;
-- A plan that still refuses the index after that means the index is missing.
-- ---------------------------------------------------------------------------
