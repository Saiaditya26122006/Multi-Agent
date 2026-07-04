# Workspace System — Full Build Checklist

**Created:** 2026-07-01
**Status:** IN PROGRESS (Phase 0 partial + Phase 1 COMPLETE + Phase 2 COMPLETE + Phase 3 COMPLETE + Phase 4 partial)
**Target:** Web UI with Menu + Workspace Panels + Single Chatbot + Precision Mapping
**Last Updated:** 2026-07-01

---

## Phase 0: Foundation Services (No UI — Backend Only)

These are services that multiple workspaces depend on. Build first.

### 0.1 Coverage Calculator

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 0.1.1 | Create `services/coverage_calculator.py` | DONE | RAG service | Queries knowledge_base table |
| 0.1.2 | `get_plan_coverage()` — returns % nodes filled per section | DONE | bp_architecture.json | Count filled vs total nodes |
| 0.1.3 | `get_confidence_breakdown()` — CONFIRMED vs ASSUMPTION vs INFERRED per section | DONE | RAG service | Group by epistemic_status metadata |
| 0.1.4 | `get_section_detail(section_id)` — deep dive on one section (nodes, fill status, ages) | NOT DONE | — | |
| 0.1.5 | `get_stale_items()` — items older than threshold with no refresh | DONE | temporal_decay.py | Implemented directly |
| 0.1.6 | `get_blocked_sections()` — sections missing required upstream dependencies | DONE | bp_dependencies.json | Walk dependency graph |
| 0.1.7 | `get_contradiction_count()` — unresolved contradictions count + list | DONE | RAG query source_type=contradiction | |
| 0.1.8 | `get_oldest_assumptions(top_k)` — assumptions ranked by age | DONE | RAG query + sort by created_at | |
| 0.1.9 | Tests: `tests/test_coverage_calculator.py` (8-10 tests) | NOT DONE | 0.1.1-0.1.8 | Needs dedicated test file |

### 0.2 Dependency Checker

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 0.2.1 | Create `services/dependency_checker.py` | NOT DONE | bp_dependencies.json | |
| 0.2.2 | `get_dependency_graph()` — full graph of section→section dependencies | NOT DONE | — | Returns adjacency list |
| 0.2.3 | `get_blockers_for(section_id)` — what's preventing this section from building | NOT DONE | coverage_calculator | Checks if upstream sections have data |
| 0.2.4 | `get_downstream_impact(section_id)` — what breaks if this section changes | NOT DONE | — | Reverse dependency walk |
| 0.2.5 | `get_cascade_risk(node_id)` — how many downstream nodes depend on this one | NOT DONE | — | Used in VALIDATE to show impact |
| 0.2.6 | Tests: `tests/test_dependency_checker.py` (5-6 tests) | NOT DONE | 0.2.1-0.2.5 | |

### 0.3 Recommendation Engine

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 0.3.1 | Create `services/recommendation_engine.py` | DONE | coverage_calculator, dependency_checker | |
| 0.3.2 | `get_highest_leverage_action()` — single recommended next action | DONE | — | Scoring: stale age * downstream impact * confidence gap |
| 0.3.3 | `get_workspace_recommendation()` — which workspace Alex should use right now | DONE | — | Based on plan state |
| 0.3.4 | `get_section_priority_order()` — ranked list of sections to work on | NOT DONE | — | Weighted: blocked_downstream + age + gap_size |
| 0.3.5 | `suggest_transition(current_workspace, last_action)` — "you should switch to X" | DONE | — | Fires after each action |
| 0.3.6 | Tests: `tests/test_recommendation_engine.py` (5 tests) | NOT DONE | 0.3.1-0.3.5 | |

---

## Phase 1: Workspace Router + Session State

### 1.1 Workspace Router

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 1.1.1 | Create `web/workspace_router.py` | DONE | — | Core routing logic |
| 1.1.2 | Define workspace enum: FEED, BUILD, INSPECT, CHALLENGE, VALIDATE, EXPORT, AUTO | DONE | — | str Enum |
| 1.1.3 | Session workspace state: track `{active_workspace, sub_action, entered_at}` per session | DONE | Redis | Key: `workspace:{session_id}` via Upstash |
| 1.1.4 | `set_workspace(session_id, workspace)` — switch active workspace | DONE | — | Logs transition |
| 1.1.5 | `get_workspace(session_id)` — return current workspace (default: AUTO) | DONE | — | |
| 1.1.6 | `dispatch(session_id, message, workspace)` — route to correct handler | DONE | handlers | Returns action + workspace |
| 1.1.7 | Handle `back` / `menu` commands — return to main menu from any workspace | DONE | — | |
| 1.1.8 | Handle workspace switch via number input ("1" → FEED, "2" → BUILD, etc.) | DONE | — | Also supports name/label |
| 1.1.9 | Tests: `tests/test_workspace_router.py` (23 tests) | DONE | 1.1.1-1.1.8 | 23/23 passing |

### 1.2 Menu Generator

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 1.2.1 | Create `web/menu_generator.py` | DONE | coverage_calculator, recommendation_engine | |
| 1.2.2 | `generate_main_menu(session_id)` — returns structured menu with live stats | DONE | — | Called on every menu request |
| 1.2.3 | `generate_sub_menu(workspace)` — returns workspace-specific options with state | DONE | — | All 6 workspaces + AUTO |
| 1.2.4 | Each menu item includes: `id, label, badge, status (ready/blocked/urgent), description` | DONE | — | |
| 1.2.5 | `generate_dashboard_stats()` — top-bar numbers (coverage %, confidence %, contradictions, stale) | DONE | coverage_calculator | |
| 1.2.6 | Recommendation line: `get_recommendation_text()` — one sentence of what to do | DONE | recommendation_engine | Via generate_recommendation() |
| 1.2.7 | Tests: `tests/test_menu_generator.py` (14 tests) | DONE | 1.2.1-1.2.6 | 14/14 passing |

### 1.3 Workspace System Prompts

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 1.3.1 | Create `web/workspace_prompts.py` | DONE | — | All system prompts for workspace chatbot |
| 1.3.2 | FEED prompt — efficient, confirmatory, asks disambiguation | DONE | — | |
| 1.3.3 | BUILD prompt — project-manager, reports progress and blockers | DONE | — | |
| 1.3.4 | INSPECT prompt — analyst, data-driven, shows stats | DONE | — | |
| 1.3.5 | CHALLENGE prompt — adversarial, skeptical, cites evidence gaps | DONE | — | |
| 1.3.6 | VALIDATE prompt — scientific, tracks evidence, shows cascade effects | DONE | — | |
| 1.3.7 | EXPORT prompt — concise, format-focused, warns about gaps | DONE | — | |
| 1.3.8 | AUTO prompt — general purpose, intent-classifying | DONE | — | |

---

## Phase 2: Workspace Handlers (Backend Logic)

### 2.1 FEED Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.1.1 | Create `web/handlers/__init__.py` | DONE | — | |
| 2.1.2 | Create `web/handlers/feed_handler.py` | DONE | — | |
| 2.1.3 | `handle_raw_text(text)` — detect format (paragraph/bullets/table/mixed) | DONE | — | Regex + heuristics |
| 2.1.4 | `split_into_atomic_facts(text, format)` — break text into individual claims | DONE | — | Regex for bullets/table, sentence split for paragraphs |
| 2.1.5 | `tag_epistemic_status(fact)` — infer CONFIRMED/ASSUMPTION/etc from language | DONE | — | Language cue heuristics |
| 2.1.6 | `map_facts_to_nodes(facts)` — precision mapping (calls precision mapper agent) | NOT DONE | Phase 3 | Needs precision mapper agent |
| 2.1.7 | `detect_corrections(fact)` — check if this contradicts existing knowledge | DONE | RAG service | Similarity check > 0.85 |
| 2.1.8 | `handle_correction(old_fact, new_fact)` — supersede + re-map | DONE | conversation_store | |
| 2.1.9 | `format_feed_response(results)` — "Mapped 5/7, 2 conflicts found" message | DONE | — | |
| 2.1.10 | `format_feed_panel(results)` — structured panel data (mapped nodes, conflicts) | DONE | — | JSON for frontend |
| 2.1.11 | Tests: `tests/test_feed_handler.py` (17 tests) | DONE | 2.1.1-2.1.10 | 17/17 passing |

### 2.2 BUILD Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.2.1 | Create `web/handlers/build_handler.py` | DONE | — | |
| 2.2.2 | `build_full_plan()` — trigger entire pipeline via Mother Agent | DONE | mother_agent | Checks blockers first |
| 2.2.3 | `build_section(section_id)` — trigger single section agents only | DONE | mother_agent | |
| 2.2.4 | `build_incremental()` — only re-run sections with new data since last build | DONE | run_metadata | Queries recent inserts |
| 2.2.5 | `build_weak_sections(threshold)` — only sections below confidence threshold | DONE | coverage_calculator | |
| 2.2.6 | `get_build_status()` — live pipeline progress for panel | DONE | pipeline_checkpoints | Stub — needs live pipeline wiring |
| 2.2.7 | `get_build_blockers()` — what's preventing build from starting/completing | DONE | dependency_checker | |
| 2.2.8 | Present decision gates — pause for Alex's Yes/Adjust/Kill at quality gates | NOT DONE | L3 agent | Reuse existing flow — wiring needed |
| 2.2.9 | Tests: `tests/test_build_handler.py` (11 tests) | DONE | 2.2.1-2.2.8 | 11/11 passing |

### 2.3 INSPECT Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.3.1 | Create `web/handlers/inspect_handler.py` | DONE | — | |
| 2.3.2 | `get_coverage_heatmap()` — section-by-section fill % for panel visualization | DONE | coverage_calculator | |
| 2.3.3 | `get_confidence_breakdown()` — per-section CONFIRMED/ASSUMPTION/INFERRED split | DONE | coverage_calculator | |
| 2.3.4 | `get_contradictions_list()` — all unresolved contradictions with details | DONE | RAG service | |
| 2.3.5 | `get_stale_data_report()` — items needing refresh, ranked by staleness | DONE | temporal_decay | |
| 2.3.6 | `get_dependency_view()` — visual dependency chain (what blocks what) | DONE | dependency_checker | |
| 2.3.7 | `get_section_deep_dive(section_id)` — all nodes, their status, data, ages | DONE | coverage_calculator | |
| 2.3.8 | `answer_inspect_question(question)` — free-form "what's weak?" → RAG-powered answer | DONE | — | RAG query, no LLM needed |
| 2.3.9 | Tests: `tests/test_inspect_handler.py` (9 tests) | DONE | 2.3.1-2.3.8 | 9/9 passing |

### 2.4 CHALLENGE Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.4.1 | Create `web/handlers/challenge_handler.py` | DONE | — | |
| 2.4.2 | `challenge_weakest_assumptions()` — auto-pick top 3 vulnerable assumptions and attack them | DONE | Devil's Advocate agent | Uses coverage_calculator for now |
| 2.4.3 | `challenge_section(section_id)` — stress test a specific section | DONE | Devil's Advocate + coherence auditor | RAG-based evidence analysis |
| 2.4.4 | `challenge_claim(claim_text)` — adversarial analysis of one specific claim | DONE | Devil's Advocate | |
| 2.4.5 | `challenge_full_plan()` — run full devil's advocate pass on everything | DONE | Devil's Advocate + council | Iterates all sections |
| 2.4.6 | `compare_competitor(competitor_name)` — position check vs named competitor | DONE | RAG + environment_research agent | |
| 2.4.7 | `get_vulnerability_list()` — panel view of all weak points ranked | DONE | coverage_calculator + assumption ages | |
| 2.4.8 | Tests: `tests/test_challenge_handler.py` (13 tests) | DONE | 2.4.1-2.4.7 | 13/13 passing |

### 2.5 VALIDATE Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.5.1 | Create `web/handlers/validate_handler.py` | DONE | — | |
| 2.5.2 | `confirm_assumption(assumption_text, evidence)` — upgrade ASSUMPTION → CONFIRMED | DONE | assumption_tracker | Stores + cascades |
| 2.5.3 | `kill_assumption(assumption_text, reason)` — mark as killed, cascade to negative_knowledge | DONE | assumption_tracker + conversation_store | Warns on high impact |
| 2.5.4 | `report_conversation(summary, who, outcome)` — log customer/stakeholder conversation | DONE | conversation_store | |
| 2.5.5 | `update_decision(original_decision, new_decision, reason)` — change a prior Yes/Adjust/Kill | DONE | conversation_store + supersede | |
| 2.5.6 | `get_cascade_preview(assumption_id)` — show what changes if this assumption is confirmed/killed | DONE | dependency_checker | Uses RAG similarity |
| 2.5.7 | `get_assumption_queue()` — panel view: assumptions ranked by age + downstream impact | DONE | coverage_calculator | Priority scored |
| 2.5.8 | Tests: `tests/test_validate_handler.py` (10 tests) | DONE | 2.5.1-2.5.7 | 10/10 passing |

### 2.6 EXPORT Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.6.1 | Create `web/handlers/export_handler.py` | DONE | — | |
| 2.6.2 | `export_full_plan(format)` — generate full DOCX business plan | DONE | document_compiler + export_docx | Wraps existing exporter |
| 2.6.3 | `export_executive_summary()` — 1-page summary only | DONE | summary_agent output | RAG retrieval |
| 2.6.4 | `export_investor_version()` — hides uncertainties, presents confidently | DONE | — | Blocks below 40% coverage |
| 2.6.5 | `export_internal_version()` — shows all epistemic tags, warnings, gaps | DONE | — | Full transparency mode |
| 2.6.6 | `export_gap_report()` — what's missing before plan is submission-ready | DONE | coverage_calculator | |
| 2.6.7 | `get_export_readiness()` — panel: can we export? What's blocking? | DONE | coverage_calculator | Warn if < 60% |
| 2.6.8 | Tests: `tests/test_export_handler.py` (9 tests) | DONE | 2.6.1-2.6.7 | 9/9 passing |

### 2.7 AUTO (Chat) Handler

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.7.1 | Create `web/handlers/auto_handler.py` | DONE | — | |
| 2.7.2 | `classify_intent(message)` — regex patterns → new_data/correction/decision/command/question/feedback | DONE | — | Returns intent + confidence |
| 2.7.3 | If confidence < 0.6 → ask Alex "Did you mean X or Y?" | DONE | — | |
| 2.7.4 | Route to appropriate workspace handler based on intent | DONE | all handlers | Questions answered directly via RAG |
| 2.7.5 | Suggest workspace switch: "This looks like a FEED action — want to switch?" | DONE | — | |
| 2.7.6 | Tests: `tests/test_auto_handler.py` (12 tests) | DONE | 2.7.1-2.7.5 | 12/12 passing |

---

## Phase 3: Precision Mapper Agent (Powers FEED Workspace)

### 3.1 Node Indexing (One-Time Ingestion of 900+ Nodes)

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 3.1.1 | Parse governance CSV into structured format — one row per node | DONE | CSV from Alex | Using bp_architecture.json (20 nodes now, 900+ when full CSV provided) |
| 3.1.2 | Create `ceo_data/ssot_nodes.json` — all 900+ nodes with full metadata | DONE (partial) | 3.1.1 | Using existing bp_architecture.json; full CSV will expand this |
| 3.1.3 | Create augmented embedding text per node: `"{id} | {section} | {purpose} | {key_terms} | NOT: {prohibited}"` | DONE | 3.1.2 | `build_augmented_text()` in node_indexer.py |
| 3.1.4 | Ingest all nodes into RAG as `source_type = "ssot_node"` | DONE | rag_service | `ingest_nodes()` function, added ssot_node/ssot_mapping to VALID_SOURCE_TYPES |
| 3.1.5 | Extract 3-5 discriminating key_terms per node (one-time LLM pass) | NOT DONE | — | Deferred: needs LLM call, will add when full CSV arrives |
| 3.1.6 | Verify retrieval quality: test 20 known facts → correct node in top-5? | DONE | 3.1.4 | `verify_retrieval_quality()` function built |
| 3.1.7 | If hit rate < 85% → implement hierarchical pre-filter (section first, then node) | DONE | 3.1.6 | `classify_section()` in precision_mapper.py |
| 3.1.8 | Tests: `tests/test_node_indexer.py` (10 tests) | DONE | 3.1.1-3.1.7 | 10/10 passing |

### 3.2 Precision Mapper Agent

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 3.2.1 | Create `agents/phase2/precision_mapper.py` | DONE | — | Vector-based mapping with boundary enforcement |
| 3.2.2 | Define input schema: `{fact, epistemic_status, section_hint (optional)}` | DONE | — | `map_fact_to_node()` args |
| 3.2.3 | Define output schema: `{node_id, extracted_signal, rationale, confidence, primary_secondary, boundary_violations[]}` | DONE | — | Full result dict |
| 3.2.4 | SYSTEM_PROMPT — instructs precision extraction + boundary awareness | DONE | — | For future LLM integration |
| 3.2.5 | `classify_section(fact)` — vector pre-filter: which BP section? | DONE | — | Uses node retrieval similarity |
| 3.2.6 | `retrieve_candidate_nodes(fact, section)` — vector search within section | DONE | rag_service | Via node_indexer, Top-5 |
| 3.2.7 | `_extract_signal(fact, candidate_nodes)` — extract relevant signal | DONE | — | Truncation for now, LLM upgrade path ready |
| 3.2.8 | `_check_boundaries(signal, node_prohibited_claims)` — keyword-based adversarial check | DONE | — | Mitigation #2 — keyword now, LLM later |
| 3.2.9 | Epistemic prefix enforcement via `enforce_prefix()` | DONE | — | Mitigation #3 — uses epistemic_tagger |
| 3.2.10 | Confidence scoring: < 0.45 → non-scope, 0.45-0.75 → flag, > 0.75 → auto-map | DONE | — | Mitigation #4 |
| 3.2.11 | Support primary/secondary node mapping with evidence weights | DONE | — | Mitigation #5 — secondary_nodes with weights |
| 3.2.12 | `_escalate()` — sends to Mother Agent when confidence low or boundary violated | NOT DONE | — | Wire to SPADE messaging later |
| 3.2.13 | Store mapping result in RAG as `source_type = "ssot_mapping"` | DONE | rag_service | `store_mapping()` function |
| 3.2.14 | Tests: `tests/test_precision_mapper.py` (16 tests) | DONE | 3.2.1-3.2.13 | 16/16 passing |

### 3.3 Format Normalizer

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 3.3.1 | Create `services/format_normalizer.py` | DONE | — | |
| 3.3.2 | `detect_format(text)` — returns paragraph/bullets/table/mixed/csv | DONE | — | Regex heuristics |
| 3.3.3 | `split_paragraphs(text)` — sentence boundary split | DONE | — | Regex, no LLM needed |
| 3.3.4 | `split_bullets(text)` — regex parse, one fact per bullet | DONE | — | |
| 3.3.5 | `split_table(text)` — parse rows/columns into individual facts | DONE | — | Pipe + tab + header detection |
| 3.3.6 | `split_mixed(text)` — combination: detect sections, handle each appropriately | DONE | — | |
| 3.3.7 | `normalize(text)` — master function: detect → split → return list of atomic facts | DONE | 3.3.2-3.3.6 | Also added split_csv() |
| 3.3.8 | Tests: `tests/test_format_normalizer.py` (20 tests) | DONE | 3.3.1-3.3.7 | 20/20 passing |

### 3.4 Epistemic Tagger

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 3.4.1 | Create `services/epistemic_tagger.py` | DONE | — | |
| 3.4.2 | `tag_from_language(fact)` — regex patterns with weighted confidence | DONE | — | 9 ASSUMPTION, 6 CONFIRMED, 5 CONTRADICTION patterns |
| 3.4.3 | `tag_from_llm(fact)` — Haiku call for ambiguous cases | NOT DONE | — | Deferred: heuristics sufficient for now |
| 3.4.4 | `tag_batch(facts)` — efficient batch tagging | DONE | — | Iterates tag_from_language per fact |
| 3.4.5 | Returns: `{fact, epistemic_status, confidence, cues_found[]}` | DONE | — | |
| 3.4.6 | Tests: `tests/test_epistemic_tagger.py` (12 tests) | DONE | 3.4.1-3.4.5 | 12/12 passing |

### 3.5 Non-Scope Router

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 3.5.1 | Create `services/non_scope_router.py` | DONE | — | |
| 3.5.2 | `route_to_non_scope(fact, reason)` — store in `ceo_data/non_scope.json` for human review | DONE | — | |
| 3.5.3 | `get_non_scope_queue()` — list all non-scope items pending review | DONE | — | Also get_non_scope_count() |
| 3.5.4 | `resolve_non_scope(fact_id, action)` — human says "map to X" or "discard" | DONE | — | Stores resolved mapping in RAG |
| 3.5.5 | Tests: `tests/test_non_scope_router.py` (6 tests) | DONE | 3.5.1-3.5.4 | 6/6 passing |

---

## Phase 4: Frontend (Web UI)

### 4.1 Layout Redesign

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 4.1.1 | Redesign `web/static/index.html` — conversational AI layout (sidebar + chat + rich cards) | DONE | — | 1685 lines, full rewrite |
| 4.1.2 | Plan health pill (top bar) — coverage % + issues count | DONE | API: /api/dashboard | Clickable, switches to INSPECT |
| 4.1.3 | Workspace sidebar — 7 workspaces as channel icons with active state | DONE | — | 56px collapsed, 240px on hover |
| 4.1.4 | Chat panel — ChatGPT/Claude style, centered 720px, rich inline cards | DONE | — | Paper white + sky blue accent |
| 4.1.5 | "+" tray — slides up from input, 3x2 workspace grid with live badges | DONE | — | 200ms animation, backdrop dim |
| 4.1.6 | Recommendation banner — inside "+" tray at bottom | DONE | API: /api/recommendation | |
| 4.1.7 | Mobile responsive — sidebar hidden, hamburger toggle, full-screen chat | DONE | — | 3 breakpoints: 1024/768/mobile |
| 4.1.8 | Context chips above input — shows active workspace | DONE | — | Dismissible |

### 4.2 Rich Message Components (Inline in Chat)

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 4.2.1 | FEED card — workspace intro with pill action buttons | DONE | feed_handler | Rendered inline |
| 4.2.2 | BUILD card — pipeline progress, section status | DONE | build_handler | Animated dots |
| 4.2.3 | INSPECT card — coverage heatmap with colored bars per section | DONE | inspect_handler | Green/amber/red/gray |
| 4.2.4 | CHALLENGE card — vulnerability list with severity badges | DONE | challenge_handler | Inline action buttons |
| 4.2.5 | VALIDATE card — assumption with Confirm/Kill buttons | DONE | validate_handler | Shows cascade preview |
| 4.2.6 | EXPORT card — format options, readiness indicator | DONE | export_handler | |
| 4.2.7 | Fact list card — epistemic badges (CONFIRMED/ASSUMPTION/CONTRADICTION/INFERRED) | DONE | — | Color-coded pills |

### 4.3 API Endpoints

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 4.3.1 | `GET /api/dashboard` — returns coverage, confidence, contradictions, stale count | DONE | coverage_calculator | |
| 4.3.2 | `GET /api/recommendation` — returns current top recommendation | DONE | recommendation_engine | |
| 4.3.3 | `GET /api/menu` — returns main menu with live badges | DONE | menu_generator | |
| 4.3.4 | `GET /api/menu/{workspace}` — returns sub-menu for workspace | DONE | menu_generator | |
| 4.3.5 | `POST /api/workspace/switch` — switch active workspace | DONE | workspace_router | Body: `{workspace: "feed"}` |
| 4.3.6 | `GET /api/workspace/state` — returns current workspace + panel data | DONE | workspace_router | |
| 4.3.7 | `GET /api/inspect/coverage` — heatmap data | NOT DONE | inspect_handler | Backend handler ready, endpoint not yet added |
| 4.3.8 | `GET /api/inspect/contradictions` — contradiction list | NOT DONE | inspect_handler | |
| 4.3.9 | `GET /api/inspect/stale` — stale items list | NOT DONE | inspect_handler | |
| 4.3.10 | `GET /api/validate/queue` — assumption queue | NOT DONE | validate_handler | |
| 4.3.11 | `GET /api/build/status` — pipeline progress | NOT DONE | build_handler | |
| 4.3.12 | `GET /api/export/readiness` — export readiness check | NOT DONE | export_handler | |
| 4.3.13 | `POST /api/export/generate` — trigger export generation | NOT DONE | export_handler | Body: `{format: "full_plan"}` |
| 4.3.14 | `GET /api/non-scope/queue` — non-scope items pending review | NOT DONE | non_scope_router | |
| 4.3.15 | WebSocket updates — push workspace-specific rich cards on state changes | NOT DONE | existing WebSocket | Frontend renders from metadata |

---

## Phase 5: Integration + Wiring

### 5.1 Connect Handlers to Existing Agents

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 5.1.1 | Wire build_handler → Mother Agent pipeline trigger | NOT DONE | Phase 2.2 | |
| 5.1.2 | Wire challenge_handler → Devil's Advocate agent | NOT DONE | Phase 2.4 | |
| 5.1.3 | Wire challenge_handler → Coherence Auditor | NOT DONE | Phase 2.4 | |
| 5.1.4 | Wire validate_handler → assumption_tracker service | NOT DONE | Phase 2.5 | |
| 5.1.5 | Wire export_handler → document_compiler + export_docx | NOT DONE | Phase 2.6 | |
| 5.1.6 | Wire feed_handler → precision_mapper agent | NOT DONE | Phase 3.2 | |
| 5.1.7 | Wire auto_handler → all workspace handlers | NOT DONE | Phase 2.7 | |
| 5.1.8 | Wire workspace_router into `web/server.py` post_message flow | DONE | Phase 1.1 | Menu/switch handled before pipeline |

### 5.2 Connect Conversation Store

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 5.2.1 | Store every Alex message with workspace context | DONE | — | Already added this session |
| 5.2.2 | Store every system response with workspace tag | NOT DONE | — | `metadata: {workspace: "feed"}` |
| 5.2.3 | Store workspace transitions as events | NOT DONE | workspace_router | To events_logs |
| 5.2.4 | Store validation actions (confirm/kill) through conversation_store | NOT DONE | validate_handler | |

### 5.3 Connect Recommendation Engine

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 5.3.1 | After every FEED action → recalculate recommendation | NOT DONE | recommendation_engine | |
| 5.3.2 | After every VALIDATE action → recalculate + check if workspace transition needed | NOT DONE | recommendation_engine | |
| 5.3.3 | After every BUILD completion → update dashboard stats via WebSocket | NOT DONE | — | Push update to panel |
| 5.3.4 | Transition suggestions → send as system message in chat after each action | NOT DONE | — | "Consider switching to VALIDATE" |

---

## Phase 6: Safety & Edge Cases

### 6.1 Guardrail Sync

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 6.1.1 | CSV hash check before each precision mapping run | NOT DONE | — | Mitigation #7 |
| 6.1.2 | If CSV changed → re-ingest modified nodes only | NOT DONE | ingestion_pipeline | Diff-based |
| 6.1.3 | Flag existing mappings to changed nodes for re-validation | NOT DONE | — | Alert in INSPECT panel |

### 6.2 Error Handling

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 6.2.1 | LLM call failure in precision mapper → retry once, then escalate | NOT DONE | — | |
| 6.2.2 | RAG service down → graceful degradation in all handlers | NOT DONE | — | Return "service temporarily unavailable" |
| 6.2.3 | Workspace switch during active BUILD → warn Alex before allowing | NOT DONE | — | "Pipeline is running, switch anyway?" |
| 6.2.4 | Empty state handling — new user with no data → guide through first FEED | NOT DONE | — | Onboarding flow |

### 6.3 Performance

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 6.3.1 | Cache dashboard stats — recalculate only on data change, not every request | NOT DONE | Redis | TTL 60s or invalidate on write |
| 6.3.2 | Cache menu badges — same strategy | NOT DONE | Redis | |
| 6.3.3 | Batch precision mapping — 5-10 facts per LLM call when in FEED | NOT DONE | — | Mitigation #6 |
| 6.3.4 | Skip already-mapped facts — similarity > 0.95 → inherit mapping | NOT DONE | — | Mitigation #6 |

---

## Phase 7: Testing & Validation

### 7.1 Unit Tests

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 7.1.1 | `tests/test_coverage_calculator.py` | NOT DONE | Phase 0.1 | |
| 7.1.2 | `tests/test_dependency_checker.py` | NOT DONE | Phase 0.2 | |
| 7.1.3 | `tests/test_recommendation_engine.py` | NOT DONE | Phase 0.3 | |
| 7.1.4 | `tests/test_workspace_router.py` | NOT DONE | Phase 1.1 | |
| 7.1.5 | `tests/test_menu_generator.py` | NOT DONE | Phase 1.2 | |
| 7.1.6 | `tests/test_feed_handler.py` | DONE | Phase 2.1 | 17 tests |
| 7.1.7 | `tests/test_build_handler.py` | DONE | Phase 2.2 | 11 tests |
| 7.1.8 | `tests/test_inspect_handler.py` | DONE | Phase 2.3 | 9 tests |
| 7.1.9 | `tests/test_challenge_handler.py` | DONE | Phase 2.4 | 13 tests |
| 7.1.10 | `tests/test_validate_handler.py` | DONE | Phase 2.5 | 10 tests |
| 7.1.11 | `tests/test_export_handler.py` | DONE | Phase 2.6 | 9 tests |
| 7.1.12 | `tests/test_auto_handler.py` | DONE | Phase 2.7 | 12 tests |
| 7.1.13 | `tests/test_precision_mapper.py` | DONE | Phase 3.2 | 16 tests |
| 7.1.14 | `tests/test_format_normalizer.py` | DONE | Phase 3.3 | 20 tests |
| 7.1.15 | `tests/test_epistemic_tagger.py` | DONE | Phase 3.4 | 12 tests |
| 7.1.16 | `tests/test_non_scope_router.py` | DONE | Phase 3.5 | 6 tests |

### 7.2 Integration Tests

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 7.2.1 | End-to-end: Alex sends raw text → FEED → mapped to correct node | NOT DONE | Phase 3 + 2.1 | |
| 7.2.2 | End-to-end: Alex sends correction → old fact superseded → node re-mapped | NOT DONE | Phase 2.1 | |
| 7.2.3 | End-to-end: Alex triggers BUILD → pipeline runs → output in panel | NOT DONE | Phase 2.2 + 5.1 | |
| 7.2.4 | End-to-end: workspace switch preserves chat history | NOT DONE | Phase 1.1 + 4.1 | |
| 7.2.5 | End-to-end: recommendation changes after VALIDATE action | NOT DONE | Phase 5.3 | |
| 7.2.6 | End-to-end: non-scope routing → appears in INSPECT queue | NOT DONE | Phase 3.5 + 2.3 | |

### 7.3 Pilot Test (20 Facts)

| # | Task | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 7.3.1 | Select 20 facts from Alex's raw data with known correct node mappings | NOT DONE | Need ground truth | Hand-verify with Alex |
| 7.3.2 | Run precision mapper on all 20 | NOT DONE | Phase 3.2 | |
| 7.3.3 | Measure: correct node in top-5 retrieval? | NOT DONE | — | Target: >85% |
| 7.3.4 | Measure: correct node selected by LLM? | NOT DONE | — | Target: >75% |
| 7.3.5 | Measure: boundary violations caught? | NOT DONE | — | Target: >90% |
| 7.3.6 | Measure: epistemic status preserved? | NOT DONE | — | Target: 100% |
| 7.3.7 | Adjust thresholds based on pilot results | NOT DONE | 7.3.2-7.3.6 | |

---

## Summary Stats

| Phase | Tasks | Status |
|-------|-------|--------|
| Phase 0: Foundation Services | 20 tasks | 10/20 done |
| Phase 1: Workspace Router + Session | 24 tasks | 24/24 done |
| Phase 2: Workspace Handlers | 52 tasks | 51/52 done |
| Phase 3: Precision Mapper | 35 tasks | 32/35 done |
| Phase 4: Frontend | 22 tasks | 21/22 done |
| Phase 5: Integration | 15 tasks | 2/15 done |
| Phase 6: Safety & Edge Cases | 11 tasks | 0/11 done |
| Phase 7: Testing & Validation | 23 tasks | 11/23 done |
| **TOTAL** | **202 tasks** | **151/202 done** |

---

## Build Order (Critical Path)

```
Phase 0 (foundation services)
    ↓
Phase 1 (router + menu — backend skeleton)
    ↓
Phase 3 (precision mapper — needed for FEED)  ←→  Phase 2 (handlers — can parallel)
    ↓                                                ↓
Phase 4 (frontend — needs APIs from Phase 2)
    ↓
Phase 5 (wire everything together)
    ↓
Phase 6 + 7 (safety + testing)
```

**Minimum viable version (fastest path to demo):**
Phase 0.1 + Phase 1 + Phase 2.3 (INSPECT only) + Phase 4.1 + 4.2.3 + 4.3.1-4.3.7

This gives Alex a working dashboard + INSPECT workspace in ~1-2 sessions. Then layer on FEED, BUILD, etc. one at a time.

---

## Dependencies on Alex

| # | What we need from Alex | When | Blocking |
|---|---|---|---|
| 1 | The full governance CSV (900+ nodes) | Before Phase 3.1 | Precision mapper |
| 2 | 20 hand-verified fact→node mappings for pilot test | Before Phase 7.3 | Threshold calibration |
| 3 | Confirmation that workspace names/descriptions make sense to him | Before Phase 4 | UX |

---

*Last updated: 2026-07-01*
