# Multi-Agent AI System — CLAUDE.md

## Project State (as of 2026-05-26)

- **Phase:** 2 (active since May 19, 2026)
- **Phase 1:** Complete — L0/L1/L3 pipeline works, Telegram integration done
- **Phase 2:** Mother Agent + 9 child agents built, SPADE messaging, SimPy simulation
- **Current bug (FIXED):** Child agents escalating with `weak_evidence` because Claude via Bedrock returns non-JSON text that fails Pydantic validation. Fix: hardened SYSTEM_PROMPTs + `_parse_llm_response()` with markdown stripping and fallback defaults.

## Technology Stack

| Component         | Technology                    |
|-------------------|-------------------------------|
| Agent framework   | SPADE (spade.agent)           |
| LLM               | Claude (Bedrock) — Sonnet/Haiku |
| Canonical DB      | Supabase / Postgres           |
| RAG / Knowledge   | Supabase pgvector + sentence-transformers (all-MiniLM-L6-v2) |
| Session memory    | Redis (Upstash)               |
| Simulation        | SimPy (Monte Carlo)           |
| MVP UI            | Streamlit                     |
| CEO channel       | Web interface (FastAPI + WebSocket) |
| Messaging         | XMPP via SPADE                |

## Run Commands

```bash
pip install -r requirements.txt
streamlit run app.py
pytest tests/
python main.py  # starts web server + pipeline
python -m services.ingestion_pipeline  # ingest CEO data into RAG
```

## Phase 2 Architecture

### Agent Files (all in `agents/phase2/`)

| Agent | File | Section | Model |
|-------|------|---------|-------|
| Mother Agent | `mother_agent.py` | orchestrator | Sonnet |
| Opportunity Analyst | `opportunity_analyst.py` | 1 | Sonnet |
| Environment Research | `environment_research.py` | 3 | Haiku |
| Organisation Designer | `organisation_designer.py` | 4 | Haiku |
| SWOT Synthesizer | `swot_synthesizer.py` | 5 | Sonnet |
| Marketing Strategy | `marketing_strategy.py` | 8 | Sonnet |
| Operations | `operations.py` | 10 | Haiku |
| Financial Modelling | `financial_modelling.py` | 12 | Sonnet |
| Launch & Contingency | `launch_contingency.py` | 13 | Haiku |
| Summary Agent | `summary_agent.py` | exec summary | Haiku |

### Schema Files

- Input schemas: `schemas/inputs/<agent_name>.py`
- Output schemas: `schemas/outputs/<agent_name>.py`
- All outputs use Pydantic BaseModel with `Literal` types for enums

### Key Patterns in Child Agents

Each child agent has:
1. `SYSTEM_PROMPT` — includes JSON-only instruction with exact field list
2. `ListenBehaviour` — SPADE CyclicBehaviour listening for messages
3. `handle_request()` — validates input, calls LLM, parses response, validates output
4. `_parse_llm_response()` — strips markdown code blocks, attempts JSON parse, falls back to schema-valid defaults
5. `_call_llm()` — calls Bedrock `invoke_model` API
6. `_escalate()` — sends escalation to Mother Agent

### LLM via AWS Bedrock

- Region: `us-east-1` (env: `AWS_BEDROCK_REGION`)
- Sonnet model: `claude-sonnet-4-20250514` (env: `CLAUDE_SONNET_MODEL`)
- Haiku model: `claude-haiku-4-5-20251001` (env: `CLAUDE_HAIKU_MODEL`)
- API: `bedrock.invoke_model(model=, max_tokens=, system=, messages=)`
- Response format: `response["body"].read()` → JSON with `content[0].text`

### SPADE Messaging Protocol

Performatives: `request`, `inform`, `escalate`, `propose`, `refuse`
Metadata fields: `performative`, `task_id`, `session_id`, `pipeline_run_id`

## Code Rules

- `snake_case` everywhere
- Never hardcode API keys — `.env` + `python-dotenv`
- Type hints on every function
- `logging` module only — never `print()`
- `Black` formatting, line length 88
- Every agent action writes to `events_logs`

## RAG System (added 2026-06-30)

### Architecture

```
CEO Data (ceo_data/*.json) → Ingestion Pipeline → Supabase pgvector (knowledge_base table)
Conversations/Decisions → conversation_store.py → Same table
Agent Insights/Run Metadata → rag_hooks.py → Same table
                                                    ↓
All Agents ← rag_mixin.py/rag_service.py ← Semantic Retrieval (top-k cosine similarity)
```

### Key Files

| File | Purpose |
|------|---------|
| `services/rag_service.py` | Core: embed, store, retrieve, supersede, batch_store |
| `services/ingestion_pipeline.py` | Chunks CEO data → embeds → stores in Supabase |
| `services/conversation_store.py` | Stores CEO messages, decisions, corrections |
| `services/rag_hooks.py` | Stores agent insights, negative knowledge, run metadata, research cache |
| `services/preference_extractor.py` | Detects CEO preference patterns from decisions |
| `services/assumption_tracker.py` | Tracks assumption lifecycle (ASSUMPTION → VALIDATED/KILLED) |
| `services/temporal_decay.py` | Freshness scoring, staleness detection |
| `agents/phase2/rag_mixin.py` | Lightweight helper for child agents to query RAG |
| `ceo_data/loader.py` | RAG-backed loader (falls back to JSON if RAG unavailable) |
| `database/migrations/add_knowledge_base.sql` | Table + indexes |
| `database/migrations/add_knowledge_base_rpc.sql` | Vector similarity RPC function |

### 12 RAG Layers (source_type values)

| source_type | What It Stores |
|-------------|---------------|
| `ceo_doc` | Alex's static data (Source-of-Truth doc, spreadsheet) |
| `conversation` | CEO messages, Q&A pairs |
| `decision` | Yes/Adjust decisions + reasoning |
| `negative_knowledge` | Killed ideas (never re-suggest) |
| `correction` | CEO overrides (supersedes old facts) |
| `feedback` | CEO feedback on outputs |
| `agent_insight` | Key findings from pipeline runs |
| `preference_pattern` | Derived CEO preferences |
| `external_research` | Cached web search results (stale_after_90_days) |
| `assumption_lifecycle` | Evidence events for/against assumptions |
| `contradiction_resolution` | Resolved contradictions (DA skips these) |
| `run_metadata` | Pipeline run summaries |

### How Agents Use RAG

- **Mother Agent**: `_assemble_input_package()` calls `get_relevant_ceo_data()` → RAG retrieval. Also injects `prohibited_claims` and checks `negative_knowledge`.
- **BaseChildAgent** (7 agents): `_rag_enrich()` called automatically in `handle_request()`.
- **Standalone agents** (9 agents): Import `rag_enrich()` from `agents/phase2/rag_mixin.py`, call at start of `handle_request()`.
- **Devil's Advocate**: Queries resolved contradictions + prohibited claims before challenging.

### Testing

```bash
pytest tests/test_rag_service.py tests/test_ingestion.py tests/test_conversation_store.py tests/test_temporal_decay.py tests/test_rag_hooks.py tests/test_preference_extractor.py tests/test_assumption_tracker.py tests/test_rag_integration.py tests/test_rag_performance.py -v
```

71 tests, all passing. See `tests/RAG_TESTING_CHECKLIST.md` for details.

## Known Issues / Watch Items

- Financial agent depends on `simulation/financial_sim.py` — ensure `run_simulation()` returns `runs_completed`, `probability_distribution`, `primary_risk_factor`
- Summary agent `executive_summary` field has min_length=200 — fallback text must meet this
- Bedrock `invoke_model` API uses positional kwargs — not the `converse` API
- RAG retrieval latency is ~400ms (remote Supabase) — acceptable but monitor under load
- Embedding model (all-MiniLM-L6-v2) gives cosine ~0.35-0.45 for related-but-different-wording texts — threshold set to 0.4 for balance
