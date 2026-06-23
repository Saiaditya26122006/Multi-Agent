# Multi-Agent AI System — Project State

**Last updated:** 2026-06-23
**Phase:** 2 (Active). Phase 1 complete and stable.
**Deployment:** Railway — web-production-9928d.up.railway.app (auto-deploys from main)
**Latest commit:** ca66a74

---

## 1. PHASE 1 — Complete, All Bugs Fixed

| Component | Status |
|-----------|--------|
| L0 Input Guard | Stable |
| L1 Clarity Agent | Stable |
| L3 Feedback Agent | Stable |
| Session state machine (7 states) | Stable |
| Web chat interface | Stable |
| Telegram integration | Stable |

**Key behaviors confirmed working:**

- **Topic-change detection:** Word-overlap heuristic + explicit trigger phrases ("new idea:", "start fresh", etc.). Heuristic suppressed during active conversation states (NEEDS_CLARIFICATION, AWAITING_RESEARCH, etc.) — only explicit triggers work mid-conversation.
- **idea_text column** added to sessions table (migration: `database/migrations/add_idea_text_to_sessions.sql`).
- **L1 context scoped to current session only** — `get_open_business_plan_sections()` and `get_pending_decisions()` filter by session_id.
- **Memory profile removed** from L1 question-generation prompt entirely.
- **EpistemicOS operating constraints** (strategic_priorities, known_constraints) removed from L1 prompt — L1 only sees name and output_style.
- **Redis resilience:** L1 has no Redis dependency in question path.
- **Dual channel:** Web chat + Telegram both working, sharing Supabase session state.

---

## 2. PHASE 2 — Built, Eval Runner Proven, Orchestration Not Yet End-to-End

### Pipeline Performance

11-section pipeline proven: **9.5/10**, milestone tagged (`milestone-11-section-working`, commit `979f4fe`).

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
| Tech Stack | §6.5 | agent_executable (code exists, not in roster) |
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

**Execution groups** (from `config/phase2/agent_roster.yaml`):
1. **Foundation:** opportunity_analyst, entrepreneur_team, organisation_designer (parallel)
2. **Evidence building:** environment_research, rd_technology, marketing_strategy (parallel, depends on G1)
3. **Strategy synthesis:** swot_synthesizer → alliances → marketing_strategy → quality_management → operations → hr_plan (sequential, depends on G1+G2)
4. **Financial and close:** financial_modelling → launch_contingency → exit_strategy → summary_agent (sequential, depends on G1+G2+G3)

### Task Object

Each task now carries:
- `task_id`
- `execution_type`: `agent_executable` | `data_retrieval` | `human_interview`
- `human_brief` (how-to-collect guidance for human_interview tasks)
- `data_source`
- `depends_on`
- `dependency_reasoning`
- `confidence_ceiling`

### Execution Type Tagging

- **data_retrieval** (outward agents, Tavily search): Sections 1, 3, 8, 12
- **human_interview** (with human_brief): Sections 2, 4, 6, 7

`dependency_map.yaml` has `dependency_reasoning` authored for all 13 sections.

### Demo Pipeline (Working End-to-End)

Phase 1 approval → task preview → Redis trigger → eval runner → docx delivered.

- `_request_gate2_approval()` rewritten to show full task transparency per task.
- `generate_preview_tasks()` in `main.py` generates Group 1 task preview synchronously before approval confirmation fires.

---

## 3. TECH STACK

| Component | Technology |
|-----------|-----------|
| LLM | AWS Bedrock — Claude Sonnet 4 + Haiku 4.5 via inference profiles, `converse()` API |
| Sonnet model ID | `us.anthropic.claude-sonnet-4-6` |
| Haiku model ID | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Region | us-east-1 |
| Database | Supabase/Postgres (11 tables) |
| Session state | Redis (Upstash) |
| Messaging (agents) | SPADE/XMPP (pending MessageBus swap) |
| Messaging (CEO) | Telegram + Web chat |
| Financial sim | SimPy Monte Carlo |
| Search | Tavily AI Search |
| Deployment | Railway (auto-deploy from GitHub main) |

---

## 4. KNOWN LATENT ISSUES (Not Blocking)

1. **question_asked column always NULL** — `create_assumption()` never writes it. `main.py` reads it in `generate_preview_tasks()` and always gets None. The question text is buried in the `statement` field as a substring instead.

2. **Dead code in `_run_pre_simulation()`** — Checks `task.get("dependencies", [])` but this field is never populated by `_generate_group_tasks()`. Loop always iterates zero times.

3. **"Challenge dependency" not tappable** — Option in task preview is text-based instruction, not an inline keyboard button.

4. **No staging environment** — Testing done against live production database.

5. **Tech Stack agent (§6.5) not in agent_roster.yaml** — Code exists at `agents/phase2/tech_stack_agent.py` but missing from roster and execution groups.

---

## 5. WHAT'S NEXT

| Item | Status |
|------|--------|
| EPI-36: RAG ingestion system | Waiting on Alex's business plan architecture table (expected June 22-23). L1-2 stable/frozen, L3+ growable. Two ingest modes: bulk + incremental add-node. |
| SPADE → MessageBus swap | Pending. Blocker before Mother Agent can run end-to-end. |
| Mother Agent end-to-end orchestration | Not yet proven — eval runner bypasses it. |
| Human gates 2 + 4 | Not started. |

---

## 6. HOW TO RUN

```bash
pip install -r requirements.txt
python main.py                    # Full Phase 2 pipeline
pytest tests/                     # Tests
streamlit run streamlit_app.py    # Internal monitoring
python web/server.py              # FastAPI web app (port 8000)
python telegram/webhook.py        # Telegram webhook listener
```

---

**Status:** Current as of 2026-06-23
**Deployment:** web-production-9928d.up.railway.app
**Agent count:** 18 (Mother + 15 child in roster + Tech Stack + Council)
**Pipeline score:** 9.5/10
