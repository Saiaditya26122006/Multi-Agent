# RAG System — Testing Checklist

**Created:** 2026-06-30  
**Status:** ALL PASSING (71/71)  
**Total Tests:** 71  
**Test Framework:** pytest  
**Last Run:** 2026-06-30 — 71 passed, 0 failed, 67s runtime  

---

## Status Legend

| Symbol | Meaning |
|--------|---------|
| [x] | NOT RUN YET |
| [~] | IN PROGRESS |
| [x] | PASSING |
| [F] | FAILING (needs fix) |
| [S] | SKIPPED (reason noted) |

---

## Test File Index

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_rag_service.py` | 16 | Core embed/store/retrieve/supersede |
| `tests/test_ingestion.py` | 8 | Chunking correctness |
| `tests/test_conversation_store.py` | 10 | CEO interaction storage |
| `tests/test_temporal_decay.py` | 10 | Freshness scoring |
| `tests/test_rag_hooks.py` | 6 | Dynamic layer hooks |
| `tests/test_preference_extractor.py` | 3 | Pattern detection |
| `tests/test_assumption_tracker.py` | 5 | Assumption lifecycle |
| `tests/test_rag_integration.py` | 7 | End-to-end flows + loader |
| `tests/test_rag_performance.py` | 4 | Speed + accuracy |

---

## 5.1 — Core RAG Service (`tests/test_rag_service.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_embed_returns_384_dims` | Embedding model produces correct dimension | [x] |
| 2 | `test_embed_normalized` | Vectors are unit-normalized for cosine similarity | [x] |
| 3 | `test_embed_different_texts_differ` | Different inputs produce different vectors | [x] |
| 4 | `test_embed_similar_texts_close` | Semantically similar text has high cosine similarity | [x] |
| 5 | `test_store_returns_uuid` | Storing a chunk returns a valid UUID | [x] |
| 6 | `test_store_deduplication` | Same content isn't stored twice | [x] |
| 7 | `test_store_invalid_source_type_raises` | Bad source_type is rejected with ValueError | [x] |
| 8 | `test_store_empty_content_skipped` | Empty strings aren't stored | [x] |
| 9 | `test_retrieve_finds_stored_content` | Round-trip: store → retrieve works | [x] |
| 10 | `test_retrieve_respects_threshold` | Low-similarity results are excluded | [x] |
| 11 | `test_retrieve_filters_source_type` | source_type filter works correctly | [x] |
| 12 | `test_retrieve_excludes_superseded` | Superseded chunks don't appear in results | [x] |
| 13 | `test_supersede_marks_old_chunk` | Supersession updates the old record | [x] |
| 14 | `test_batch_store_multiple` | Bulk insert stores all chunks | [x] |
| 15 | `test_format_chunks_for_injection` | Formatting respects max_chars limit | [x] |
| 16 | `test_content_hash_deterministic` | Same content always produces same hash | [x] |

---

## 5.2 — Ingestion Pipeline (`tests/test_ingestion.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_chunk_customers_produces_correct_count` | Customers file → correct chunk count | [x] |
| 2 | `test_chunk_contradictions_produces_9` | All 9 contradictions become chunks | [x] |
| 3 | `test_chunk_preserves_epistemic_status` | Status tags survive chunking | [x] |
| 4 | `test_chunk_bp_architecture_produces_22` | All BP nodes become chunks | [x] |
| 5 | `test_chunk_prohibited_claims_has_global` | Global prohibitions are present in chunks | [x] |
| 6 | `test_normalize_status_handles_variants` | Various status strings normalize correctly | [x] |
| 7 | `test_empty_file_produces_gap_chunk` | Files with gap=true produce a MISSING chunk | [x] |
| 8 | `test_ingest_all_ceo_data_count` | Full ingestion produces ~226 chunks | [x] |

---

## 5.3 — Conversation Store (`tests/test_conversation_store.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_store_ceo_message_basic` | CEO messages get stored and return ID | [x] |
| 2 | `test_store_ceo_message_too_short_skipped` | Messages < 5 chars are skipped | [x] |
| 3 | `test_store_decision_kill_is_negative_knowledge` | Kill decisions tagged as negative_knowledge | [x] |
| 4 | `test_store_decision_yes_is_decision_type` | Yes decisions tagged as decision | [x] |
| 5 | `test_store_correction_supersedes_old` | Corrections mark old chunk as superseded | [x] |
| 6 | `test_store_ceo_answer_pairs_qa` | Q&A pairs stored together in content | [x] |
| 7 | `test_has_been_asked_true` | Returns True when similar question was answered | [x] |
| 8 | `test_has_been_asked_false_when_no_match` | Returns False when no similar Q exists | [x] |
| 9 | `test_has_been_killed_true` | Returns True when proposal matches negative knowledge | [x] |
| 10 | `test_has_been_killed_false` | Returns False when no match exists | [x] |

---

## 5.4 — Temporal Decay (`tests/test_temporal_decay.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_is_stale_true_for_old_data` | Data older than policy threshold is stale | [x] |
| 2 | `test_is_stale_false_for_fresh_data` | Recent data is not stale | [x] |
| 3 | `test_is_stale_uses_last_confirmed` | last_confirmed date overrides created_at | [x] |
| 4 | `test_never_stale_policy` | "never_stale" policy always returns False | [x] |
| 5 | `test_recency_score_decays` | Score decreases with age | [x] |
| 6 | `test_recency_score_max_is_today` | Today's data gets score ≈ 1.0 | [x] |
| 7 | `test_status_weight_confirmed_highest` | CONFIRMED gets weight 1.0 | [x] |
| 8 | `test_status_weight_superseded_lowest` | SUPERSEDED gets weight 0.1 | [x] |
| 9 | `test_final_score_combines_all` | Combined score uses all three weights correctly | [x] |
| 10 | `test_stale_data_gets_penalized` | Stale chunks get reduced similarity score | [x] |

---

## 5.5 — RAG Hooks (`tests/test_rag_hooks.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_store_agent_insight` | Agent insights stored with correct source_type | [x] |
| 2 | `test_store_negative_knowledge` | Negative knowledge stored correctly | [x] |
| 3 | `test_store_run_metadata` | Pipeline run summaries stored with all fields | [x] |
| 4 | `test_store_external_research_has_freshness` | Research gets stale_after_90_days policy | [x] |
| 5 | `test_check_negative_knowledge_finds_match` | Killed proposals are retrievable by similarity | [x] |
| 6 | `test_check_negative_knowledge_no_false_positive` | Unrelated proposals don't match | [x] |

---

## 5.6 — Preference Extractor (`tests/test_preference_extractor.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_no_patterns_with_few_decisions` | Needs minimum 3 decisions to detect | [x] |
| 2 | `test_detects_optimistic_rejection_pattern` | Finds "optimistic" theme in rejections | [x] |
| 3 | `test_confidence_increases_with_count` | More occurrences = higher confidence | [x] |

---

## 5.7 — Assumption Tracker (`tests/test_assumption_tracker.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_record_evidence_stores_chunk` | Evidence events get stored and return ID | [x] |
| 2 | `test_invalid_effect_rejected` | Only valid effects (supports/challenges/invalidates/partially_validates) allowed | [x] |
| 3 | `test_get_status_no_evidence` | Default status is ASSUMPTION with confidence 0.3 | [x] |
| 4 | `test_get_status_after_supports` | Supporting evidence raises confidence above 0.4 | [x] |
| 5 | `test_get_status_after_invalidates` | Invalidating evidence → CONTRADICTION status | [x] |

---

## 5.8 — Integration: RAG Mixin + Loader (`tests/test_rag_integration.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_rag_enrich_returns_string` | Mixin works end-to-end against live DB | [x] |
| 2 | `test_rag_enrich_graceful_failure` | Returns "" when RAG is unavailable | [x] |
| 3 | `test_loader_uses_rag_when_available` | Updated loader calls RAG (has `_rag` key) | [x] |
| 4 | `test_loader_falls_back_to_json` | Fallback works when RAG unavailable | [x] |
| 5 | `test_conversation_round_trip` | Store CEO message → retrieve semantically | [x] |
| 6 | `test_kill_blocks_future_proposals` | Kill decision → has_been_killed returns True | [x] |
| 7 | `test_correction_supersedes_old` | After correction, only new fact is retrieved | [x] |

---

## 5.9 — Performance & Accuracy (`tests/test_rag_performance.py`)

| # | Test Name | What It Proves | Status |
|---|-----------|---------------|--------|
| 1 | `test_retrieval_latency_under_200ms` | Average retrieve() < 200ms over 10 calls | [x] |
| 2 | `test_embed_latency_under_50ms` | Average embed() < 50ms over 10 calls | [x] |
| 3 | `test_relevant_chunks_in_top_5` | "pricing model" query returns pricing chunks | [x] |
| 4 | `test_irrelevant_query_returns_few` | "quantum physics Mars" returns few/no results | [x] |

---

## Run Commands

```bash
# Run all RAG tests
pytest tests/test_rag_service.py tests/test_ingestion.py tests/test_conversation_store.py tests/test_temporal_decay.py tests/test_rag_hooks.py tests/test_preference_extractor.py tests/test_assumption_tracker.py tests/test_rag_integration.py tests/test_rag_performance.py -v

# Run only unit tests (no Supabase needed for temporal_decay + ingestion chunkers)
pytest tests/test_temporal_decay.py tests/test_ingestion.py -v

# Run integration tests (requires Supabase connection)
pytest tests/test_rag_service.py tests/test_conversation_store.py tests/test_rag_hooks.py tests/test_rag_integration.py tests/test_rag_performance.py -v

# Run a single test file
pytest tests/test_rag_service.py -v

# Run a single test
pytest tests/test_rag_service.py::test_embed_returns_384_dims -v
```

---

## Prerequisites

- [x] Supabase `knowledge_base` table created (migration run)
- [x] Supabase `match_knowledge_base` RPC function created
- [x] `sentence-transformers` installed
- [x] 226 chunks ingested in knowledge_base
- [ ] `.env` has SUPABASE_URL + SUPABASE_ANON_KEY set

---

## Summary Stats

| Metric | Count |
|--------|-------|
| Total test files | 9 |
| Total tests | 71 |
| Passing | 71 |
| Failing | 0 |
| Skipped | 0 |

---

*Last updated: 2026-06-30*
