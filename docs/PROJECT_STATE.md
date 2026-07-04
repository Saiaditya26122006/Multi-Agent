# Multi-Agent AI System — Project State

**Last updated:** 2026-07-04
**Phase:** 2 (Active). Phase 1 complete and stable.
**Deployment:** Railway — web-production-9928d.up.railway.app (auto-deploys from main)
**Interface:** Web-only (Telegram removed)

---

## 1. PHASE 1 — Complete, All Bugs Fixed

| Component | Status |
|-----------|--------|
| L0 Input Guard | Stable |
| L1 Clarity Agent | Stable |
| L3 Feedback Agent | Stable |
| Session state machine (7 states) | Stable |
| Web chat interface | Stable |

**Key behaviors confirmed working:**

- **EPI-34 — Session separation:** Topic-change detection via word-overlap heuristic + explicit trigger phrases. Heuristic suppressed during active conversation states. idea_text column in sessions table. Redis notification on new session.
- **L1 context scoped to current session only** — `get_open_business_plan_sections()` and `get_pending_decisions()` filter by session_id.
- **Memory profile removed** from L1 question-generation prompt entirely.
- **EpistemicOS operating constraints** removed from L1 prompt — L1 only sees name and output_style.
- **Redis resilience:** L1 has no Redis dependency in question path.

---

## 2. PHASE 2 — Pipeline Proven, Workspace System Live

### Pipeline Performance

11-section pipeline proven: **9.5/10**, milestone tagged (`milestone-11-section-working`, commit `979f4fe`).

### EPI-35 — Task Transparency at Approval Time

Group 1 preview fires synchronously before confirmation. Shows:
- task_id, section, execution_type, data_source
- dependency_reasoning, human_brief ("how to collect" guidance)
- confidence_ceiling
- Challenge dependency button live on web

### Workspace System (Live)

7 workspaces: Feed, Build, Inspect, Challenge, Validate, Export, Auto.

| File | Purpose |
|------|---------|
| `web/workspace_router.py` | Tracks active workspace per session in Redis, dispatches |
| `web/menu_generator.py` | Dynamic menus with live stats (coverage, contradictions, stale) |
| `web/handlers/auto_handler.py` | Intent classification + RAG question answering |
| `web/handlers/feed_handler.py` | Raw-to-structured ingestion with approval flow |
| `web/handlers/build_handler.py` | Business plan generation (full, section, incremental) |
| `web/handlers/inspect_handler.py` | Coverage, confidence, contradictions, stale, dependencies |
| `web/handlers/challenge_handler.py` | Devil's advocate / assumption stress-testing |
| `web/handlers/validate_handler.py` | Assumption confirmation/killing queue |
| `web/handlers/export_handler.py` | DOCX / investor / gap report exports |

### Feed Handler (EPI-36 — Complete)

Full raw-to-structured ingestion flow:
1. **Content type classification** — 8 types: fact, assumption, decision, risk, task, metric, constraint, open_question
2. **BP node matching** — Semantic similarity against architecture nodes (cached embeddings). Proposes at >0.6, flags as unmatched below.
3. **Approval flow** — Verbatim ADD block shown to Alex. Options: Approve / Adjust / Create new node. Pending fact stored in Redis with 10-min TTL.
4. **Write-back** — Stores verbatim text to knowledge_base via rag_service.store(). Post-store hooks: contradiction detection, negative knowledge, temporal decay.

### AUTO Workspace (Working)

- Questions classified via intent patterns, always route to auto_handler (never pipeline)
- RAG retrieval (top-10, threshold 0.38) + Haiku LLM synthesis
- Epistemic tags translated to natural language (no raw [ASSUMPTION] tags in output)
- Markdown rendering via marked.js

### RAG System (Live — 12-Layer Knowledge Base)

226 chunks in Supabase pgvector (HNSW index). all-MiniLM-L6-v2 embeddings (384 dims).

| Layer (source_type) | What It Stores |
|---------------------|---------------|
| ceo_doc | Alex's static data + feed handler approved facts |
| conversation | CEO messages (auto-stored via store_ceo_message) |
| decision | Yes/Adjust decisions + reasoning |
| negative_knowledge | Killed ideas (never re-suggest) |
| correction | CEO overrides (supersedes old facts) |
| feedback | CEO feedback on outputs |
| agent_insight | Key findings from pipeline runs |
| preference_pattern | Derived CEO preferences |
| external_research | Cached web search results |
| assumption_lifecycle | Evidence events for/against assumptions |
| contradiction_resolution | Resolved contradictions |
| run_metadata | Pipeline run summaries |

All 9 child agents wired via `rag_mixin.py`. RAG-first loader with JSON fallback. 71 tests passing.

### Agents (18 total)

| Agent | Section | Role |
|-------|---------|------|
| Mother Agent | — | Orchestrator (not yet run end-to-end) |
| Opportunity Analyst | §1 | data_retrieval (Tavily search) |
| Entrepreneur Team | §2 | human_interview |
| Environment Research | §3 | data_retrieval (Tavily search) |
| Organisation Designer | §4 | human_interview |
| SWOT Synthesizer | §5 | Synthesis (depends on §3, §4) |
| R&D Technology | §6 | human_interview |
| Alliances | §7 | human_interview |
| Marketing Strategy | §8 | data_retrieval (Tavily search) |
| Quality Management | §9 | agent_executable |
| Operations | §10 | agent_executable |
| HR Plan | §11 | agent_executable |
| Financial Modelling | §12 | data_retrieval (Tavily search) |
| Launch & Contingency | §13 | agent_executable |
| Exit Strategy | §14 | agent_executable |
| Summary Agent | exec summary | Runs last, reads all sections |
| Council | — | Quality gate (5-persona deliberation) |

---

## 3. UI

- 56px icon-only nav rail with 7 workspace icons
- Geist font + JetBrains Mono for code
- Zinc color system, dark cinema palette
- Plus-button command tray with live badges per workspace
- Marked.js markdown rendering for assistant messages
- User messages plain text, assistant messages rendered markdown
- Conversation store: every inbound message stored to RAG

---

## 4. TECH STACK

| Component | Technology |
|-----------|-----------|
| LLM | AWS Bedrock — Claude Sonnet 4 + Haiku 4.5 via `converse()` API |
| Sonnet model ID | `us.anthropic.claude-sonnet-4-6` |
| Haiku model ID | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Region | us-east-1 |
| Database | Supabase/Postgres (11 tables + knowledge_base) |
| Vector search | pgvector with HNSW index, match_knowledge_base RPC |
| Embeddings | all-MiniLM-L6-v2 (384 dims, sentence-transformers) |
| Session state | Redis (Upstash) |
| Messaging (agents) | SPADE/XMPP (pending MessageBus swap) |
| Messaging (CEO) | Web chat (FastAPI + WebSocket) |
| Financial sim | SimPy Monte Carlo |
| Search | Tavily AI Search |
| Deployment | Railway (auto-deploy from GitHub main) |

---

## 5. KNOWN GAPS (Not Yet Built)

1. **Memory index** — chunk_relationships table connecting facts automatically. Not started.

2. **BP node matching coverage** — Feed handler works but only 20 architecture nodes exist (BP.1.*). Matching improves as Alex adds BP.2-BP.14 nodes.

3. **Phase 5 workspace handlers** — Build, Challenge, Validate, Export have stubs but are not wired to real agents yet. They return mock/static data.

4. **Mother Agent end-to-end** — Eval runner still bypasses it. Not proven in production.

5. **SPADE → MessageBus swap** — Still pending. Blocker before Mother Agent can orchestrate without XMPP.

6. **Human gates 2 + 4** — Not started.

7. **No staging environment** — Testing done against live production database.

---

## 6. HOW TO RUN

```bash
pip install -r requirements.txt
python main.py                    # Full Phase 2 pipeline + web server
pytest tests/                     # Tests (71 RAG + workspace + handler tests)
streamlit run streamlit_app.py    # Internal monitoring
python -m services.ingestion_pipeline  # Ingest CEO data into RAG
```

---

**Status:** Current as of 2026-07-04
**Deployment:** web-production-9928d.up.railway.app
**Agent count:** 18 (Mother + 15 child in roster + Council)
**Pipeline score:** 9.5/10
**RAG chunks:** 226
**Workspaces:** 7 (all routing live)
