# Multi-Agent AI System — CLAUDE.md

## Project State (as of 2026-07-20)

- **Phase:** 2 (active since May 19, 2026)
- **Phase 1:** Complete — L0/L1/L3 pipeline works, Web Interface integration done
- **Phase 2:** Mother Agent + 9 child agents built, custom async message bus, SimPy simulation
- **Latest (2026-07-20):** SPADE removed (never used), BP classification error handler added for 90% accuracy target
- **Datastore split (intentional):** durable records → Supabase; **ephemeral, short-TTL workflow state → Redis**. Session flags/data moved to Supabase (survives restarts). Redis is deliberately KEPT for Feed's ephemeral state — pending-fact approvals (10 min), feed state, quarantine (7 days), batch, undo — which is a poor fit for Postgres (TTL churn, write amplification) and whose loss is harmless (Alex re-submits). Do not "finish removing" Redis; it is the right tool for that state.

## Technology Stack

| Component         | Technology                    |
|-------------------|-------------------------------|
| Agent orchestration | Custom async message bus (in-memory, no external deps) |
| LLM               | Claude (Bedrock) — Sonnet/Haiku |
| Canonical DB      | Supabase / Postgres           |
| RAG / Knowledge   | Supabase pgvector + Amazon Titan Embed v2 (1024-dim, via Bedrock) |
| Session state     | Supabase (7 new columns on sessions table) — durable |
| Ephemeral state   | Redis (Upstash) — Feed pending/quarantine/batch/undo, TTL'd |
| Simulation        | SimPy (Monte Carlo)           |
| MVP UI            | Streamlit                     |
| CEO channel       | Web interface (FastAPI + WebSocket) |
| Messaging         | MessageBus (async in-process) |

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
2. `handle_request()` — validates input, calls LLM via Bedrock, parses response, validates output
3. `_parse_llm_response()` — strips markdown code blocks, attempts JSON parse, falls back to schema-valid defaults
4. `_call_llm()` — calls Bedrock `invoke_model` API with retry + exponential backoff
5. `_escalate()` — sends escalation to Mother Agent via MessageBus
6. MessageBus receiver — async listener for incoming tasks from mother_agent

### LLM via AWS Bedrock

- Region: `us-east-1` (env: `AWS_BEDROCK_REGION`)
- Sonnet model: `claude-sonnet-4-20250514` (env: `CLAUDE_SONNET_MODEL`)
- Haiku model: `claude-haiku-4-5-20251001` (env: `CLAUDE_HAIKU_MODEL`)
- API: `bedrock.invoke_model(model=, max_tokens=, system=, messages=)`
- Response format: `response["body"].read()` → JSON with `content[0].text`

### MessageBus API

```python
await message_bus.send(
    sender="mother_agent",
    recipient="child_agent_name",
    payload={"task": "...", "data": {...}},
    session_id="sess-123",
    pipeline_run_id="run-456"
) → msg_id

msg = await message_bus.receive(recipient, timeout=30) → MessageEnvelope or None
await message_bus.send_ack(msg_id) → True/False
```

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

Before writing a new caller of `store()`/`batch_store()`, read **"Storage Write Path
— store() contract + persistence"** in `PROJECT_STATE.md` — it is the single source
of truth for the return contract and failure behaviour.

**Before building or wiring the Feed classifier**, read the **`degraded_target`
CONTRACT** at the top of `PROJECT_STATE.md`. `match_bp_architecture` returns all nodes;
the classifier — not the RPC — must refuse to auto-file into a node with
`degraded_target = TRUE` and route that fact to human review instead. 89 of the 912
architecture nodes are degraded.

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

71 tests. See `tests/RAG_TESTING_CHECKLIST.md` for details. These hit live Supabase
and Bedrock, so they are slow (~2 min) and depend on network distance to us-east-1.

## Known Issues / Watch Items

- Financial agent depends on `simulation/financial_sim.py` — ensure `run_simulation()` returns `runs_completed`, `probability_distribution`, `primary_risk_factor`
- Summary agent `executive_summary` field has min_length=200 — fallback text must meet this
- Bedrock `invoke_model` API uses positional kwargs — not the `converse` API
- RAG retrieval is ~1s, and it is dominated by embedding, not by Supabase: a warm Titan
  embed is a ~400ms Bedrock round trip from Europe/Asia and the vector search itself is
  only ~180ms. Measured from inside us-east-1 it would be far lower. Anything that
  embeds in a loop pays that per item — prefer the embedding already stored on the
  `knowledge_base` row (see `BaseChildAgent._fetch_stored_embeddings`).
- Embedding model (Amazon Titan Embed v2) outputs 1024-dim normalized vectors.
  `DEFAULT_THRESHOLD` is **0.3**. Titan similarities are compressed: rephrasings of the
  same idea measure ~0.39-0.65 and unrelated text ~0.10-0.29, so thresholds much above
  ~0.5 will silently match nothing. `has_been_killed` used 0.65 and never fired.
