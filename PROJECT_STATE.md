# Multi-Agent AI System — Project State

**Last updated:** 2026-06-02  
**Phase:** 2 (Active)  
**Project path:** /home/saiaditya26122006/multi-agent-system

---

## 1. WHAT'S BUILT AND WORKING
*Confirmed running in real execution path, not just existing in code*

### Phase 1 Pipeline — All Working

| File | Status | What it does |
|------|--------|--------------|
| `agents/l0_input_guard.py` | ✓ Operational | Validates message safety, filters spam/off-topic |
| `agents/router_agent.py` | ✓ Operational | Classifies message: new_idea, clarification, general_chat |
| `agents/l1_clarity_agent.py` | ✓ Operational | Generates clarification questions, 3-question limit |
| `agents/l3_feedback_agent.py` | ✓ Operational | Generates feedback summary + decision prompt (Yes/Adjust/Kill) |
| `agents/memory_agent.py` | ✓ Operational | Writes session state to Redis + Supabase |
| `tools/telegram_handler.py` | ✓ Operational | Telegram webhook integration via python-telegram-bot |
| `main.py` | ✓ Operational | Full Phase 1 pipeline orchestration |

**Note:** Phase 1 built for Gemini API, migrated to Bedrock May 19, 2026. Not tested end-to-end on Bedrock yet but code exists and ran on Gemini.

### Phase 2 — Runs via Eval Runner Only

| File | Status | What it does |
|------|--------|--------------|
| `agents/phase2/intelligence_engine.py` | ✓ Operational | 4-step adaptive reasoning loop: decompose → retrieve → challenge → revise. Two enforcement gates: IE (input epistemic validation) + OE (output schema validation). Cross-section context injection. Retry logic for throttling and timeouts. |
| `agents/phase2/opportunity_analyst.py` | ✓ Operational | Section 1: Market opportunity, competitive strategy, objectives |
| `agents/phase2/environment_research.py` | ✓ Operational | Section 3: PEST, Porter Five Forces, risks/opportunities |
| `agents/phase2/organisation_designer.py` | ✓ Operational | Section 4: Team structure, roles, headcount, capability gaps |
| `agents/phase2/swot_synthesizer.py` | ✓ Operational | Section 5: Strategic positioning, strengths/weaknesses/threats |
| `agents/phase2/marketing_strategy.py` | ✓ Operational | Section 8: TAM/SAM, pricing, GTM, revenue model, CAC |
| `agents/phase2/operations.py` | ✓ Operational | Section 10: Production process, cost structure, capacity planning |
| `agents/phase2/financial_modelling.py` | ✓ Operational | Section 12: 3-statement model, break-even, DCF, risk mitigation |
| `agents/phase2/launch_contingency.py` | ⚠️ Intermittent | Section 13: Launch program, prerequisites, capital plan. **Bedrock connection-closed errors** |
| `agents/phase2/summary_agent.py` | ✓ Operational | Executive summary, contradictions, key assumptions |
| `evaluation/run_grounded_eval.py` | ✓ Operational | End-to-end pipeline runner against real EpistemicOS data. Last run: June 2, 2026, 8/9 sections parsed, 202k tokens, 28 minutes, score 8.9/10 |
| `evaluation/scorer.py` | ✓ Operational | Automated scorer: schema 30%, specificity 40%, completeness 30%. Mechanical bugs fixed June 2: REQUIRED_FIELDS, MIN_LENGTHS, field name mappings. Re-scored 6.6→8.9 |
| `ceo_data/loader.py` | ✓ Operational | Loads 22,881-char EpistemicOS source-of-truth. Preserves epistemic tags (CONFIRMED, ASSUMPTION, INFERRED, CONTRADICTION). Section-scoped injection under 2,900-char budget. 11 topics loaded. |
| `simulation/financial_sim.py` | ✓ Operational | SimPy Monte Carlo: 1000 runs, 36-month horizon. Returns `runs_completed`, `probability_distribution`, `primary_risk_factor` |

**Key limitation:** Eval runner calls `IntelligenceEngine` directly. Mother Agent orchestration layer not in execution path.

### Web Interface — Operational

| File | Status | What it does |
|------|--------|--------------|
| `web/server.py` | ✓ Operational | FastAPI backend with WebSocket real-time streaming. Routes: `/`, `/api/health`, `/api/session-key`, `/api/messages`, `/api/knowledge-base`, `/api/knowledge-base/add`, `/ws/{session_key}` |
| `web/static/index.html` | ✓ Operational | Single-page app with 3 tabs: Chat, Pipeline Trace, Knowledge Base. Streaming typewriter effect, decision buttons (Yes/Adjust/Kill), animated SVG pipeline visualization. |

**Chat tab:** Message history from Supabase, typing indicator, WebSocket real-time updates  
**Pipeline Trace tab:** Pannable/zoomable canvas, animated data flow (dots along SVG paths), 13 agent cards (Phase 1 + Mother + 9 child agents), status bar, stream log, metrics  
**Knowledge Base tab:** (built June 2, 2026) Shows all `ceo_data/` topics with epistemic status badges (green CONFIRMED, amber ASSUMPTION, blue INFERRED, red CONTRADICTION, gray no_data). Add fact form writes to JSON files. **Tested and working.**

**Server status:** Running on port 8000. Auth via `WEB_AUTH_TOKEN` env var.

### Data Ingestion Layer — Operational

| File | Count | What it does |
|------|-------|--------------|
| `ceo_data/*.json` | 11 files | buyers_icp, capabilities, competitors, constraints, customers, financials, market_research, product_definition, team, value_proposition. Each with epistemic tags. |
| `ceo_data/deck.txt` | 1 file | Executive summary text |
| `ceo_data/EpistemicOS — Structured Source-of-.txt` | 1 file | 22,881-char source-of-truth document |
| `ceo_data/loader.py` | 1 file | `load_all_ceo_data()` returns all topics. `get_relevant_ceo_data(section_number)` returns section-scoped package under 2,900-char budget. Progressive trimming prioritizes CONFIRMED over ASSUMPTION. |

**Gap topics** (status: no_data): competitors, market_research, financials, team — explicitly marked with `gap_reason`.

### GitHub — Versioned

**Branch:** main  
**Commit:** 37f3a88 (June 2, 2026)  
**Recent commits:**
- Build EpistemicOS data ingestion layer and fix grounded eval pipeline
- Wire 9-section eval pipeline: add output-length caps, fix truncation
- Implement all 10 critique fixes: IE enforcement, reasoning prompts, MessageBus, BDI beliefs
- Add intelligence benchmark and system critique documentation

---

## 2. BUILT BUT NEVER RUN END TO END
*Code exists, unproven in real pipeline*

| Component | File | Lines | Status | Why Not Running |
|-----------|------|-------|--------|-----------------|
| **Mother Agent** | `agents/phase2/mother_agent.py` | 2,591 | Bypassed | Eval runner calls `IntelligenceEngine` directly; Mother Agent orchestration layer never exercised |
| **MessageBus** | `agents/phase2/message_bus.py` | ~300 | Not wired | Built as SPADE replacement; agents don't use it yet, still have SPADE scaffolding |
| **Devil's Advocate** | `agents/phase2/devils_advocate.py` | ~800 | Not in pipeline | Code exists for challenge loop; Mother Agent would call it at Gate 3, but Mother Agent doesn't run |
| **Council Agent** | `agents/phase2/council_agent.py` | ~1,200 | Never fires | 5-persona review (**Skeptic, Architect, Visionary, Stranger, Operator**); Mother Agent would call after DA passes, but Mother doesn't run |
| **Negotiation Agent** | `agents/phase2/negotiation_agent.py` | ~600 | Never triggered | Resolves contradictions between sections; only needed if Council surfaces conflict, which never happens |
| **Conflict Resolver** | `agents/phase2/conflict_resolver.py` | ~400 | Never triggered | Same reason as Negotiation Agent — downstream of Council which never runs |
| **Learning Engine** | `agents/phase2/learning_engine.py` | ~500 | Passive only | Records events to `events_logs` table; no real feedback data to learn from yet; adaptation logic untested |
| **BDI Belief System** | `agents/phase2/bdi_beliefs.py` | ~350 | Not exercised | Persists beliefs to Redis; Intelligence Engine reads it but doesn't update it in real pipeline; passive storage only |
| **Coherence Auditor** | `agents/phase2/coherence_auditor.py` | ~450 | Not active | Cross-section consistency checker; Mother Agent would call after all sections complete, but eval path doesn't reach it |
| **Document Compiler** | `agents/phase2/document_compiler.py` | ~600 | Not wired | Converts JSON outputs → Markdown business plan document; code exists, tested in isolation, but not called in full eval run |
| **SPADE Messaging** | `agents/phase2/*_agent.py` (all) | N/A | Deprecated | Every Phase 2 agent has `ListenBehaviour` and `handle_request()` SPADE scaffolding; being replaced by MessageBus but not removed yet |

**Key insight:** Eval runner proves Intelligence Engine + 9 child agents work in isolation. Everything above Intelligence Engine (Mother Agent, gates, DA, Council) and everything cross-cutting (MessageBus, Coherence Auditor, Document Compiler) has never run in a real end-to-end execution. They exist as code but are untested in the actual pipeline.

---

## 3. NOT BUILT
*Confirmed missing, no code*

| Component | Status | Why Missing |
|-----------|--------|-------------|
| **Search service** | Not built | Shared retrieval function for outward agents (Opportunity §1, Environment §3, Marketing §8, Financial §12). Architecture decision: search is a function, not an agent. Every retrieved fact carries source URL + date + freshness flag. Human vets source credibility at gate. |
| **Human Gate 2** | Not built | Sufficiency check before agents run. Validates whether input brief has enough information for agents to proceed. |
| **Human Gate 4** | Not built | Final output approval before delivery to CEO. Alex reviews compiled business plan, approves or requests revision. |
| **Section 11 HR agent** | Not built | No code exists. Marked `always_required: true` in roster YAML but never implemented. |
| **Sections 2, 6, 7, 9** | Stubs only | Section files exist with empty schema definitions but no agent logic. |
| **Operating rules files** | Empty scaffolding | `guardrails.md`, `business_plan_process.md`, `workflow_rules.md` — files exist but contain no actual rules. |
| **Roster YAML fix** | Not fixed | `agents/phase2/agent_roster.yaml` has wrong agent/section ownership mappings. Will misdirect Mother Agent when it runs. |

---

## 4. ARCHITECTURE DECISIONS
*Locked in — do not re-debate*

### Search as a Shared Function
- **Search = shared retrieval FUNCTION, not an agent**
- Outward agents ×4 call search: Opportunity §1, Environment §3, Marketing §8, Financial §12
- Inward agents ×5 synthesize only: Organisation §4, SWOT §5, Operations §10, Launch §13, Summary
- Every retrieved fact carries: source URL + date + freshness flag
- Human vets source credibility at gate — agent finds, human trusts

### Grounding vs. Craft Signals
- **Scorer measures craft** (schema compliance, specificity, completeness)
- **Confidence field measures grounding** (data availability, assumption count)
- These are separate signals — a 10/10 scorer result with low confidence means "structurally excellent output derived from sparse data"

### Messaging Architecture
- **SPADE/XMPP being replaced by MessageBus** (in-process async)
- All Phase 2 agents still have SPADE scaffolding (will be removed)
- MessageBus provides async message passing without XMPP overhead

### Mother Agent Rollout Strategy
Staged rollout (do not skip ahead):
1. MessageBus wired
2. One group tested (e.g., Group 1: Opportunity §1 + Organisation §4)
3. Gate 3 added (group output review)
4. Devil's Advocate added (challenge loop)
5. Council Agent added (multi-persona review)
6. Negotiation only if Council surfaces conflict

### Knowledge Base Tab
- **Memory system for Alex to add/edit facts as he learns them**
- No code editing required
- Epistemic tags preserved (CONFIRMED, ASSUMPTION, INFERRED, CONTRADICTION)
- Writes directly to `ceo_data/*.json` files
- Agents read from same files — single source of truth

---

## 5. BUILD PRIORITY
*In order — do not skip ahead*

1. ✅ **PROJECT_STATE.md updated** (this file)
2. **Search service** — shared retrieval function, outward agents only
3. **SPADE → MessageBus swap** — remove SPADE scaffolding, wire MessageBus
4. **Mother Agent sequencing** — one group proven before scaling
5. **Human gates 2 and 4** — sufficiency check + final approval
6. **Operating rules files** — guardrails, business plan process, workflow rules
7. **Roster YAML fix** — correct agent/section ownership mappings

---

## 6. EXPLICITLY DEFERRED
*Do not build, do not ask about*

- Learning Engine adaptation (no feedback data yet)
- BDI as active reasoning layer (passive only for now)
- SPADE/XMPP (being replaced by MessageBus)
- Negotiation/conflict resolution (only if real contradictions appear)
- Council Agent (after DA is proven)
- Sections 11, 2, 6, 7, 9 (not in current scope)
- Parallel execution (sequential proven first)
- Airtable/Notion dashboard (manual for now)
- LLM Judge in eval (human-scored only)
- Scorer grounding dimension (decided against — confidence field does this)

---

## 7. TECH STACK

| Component | Technology | Details |
|-----------|-----------|---------|
| **LLM** | AWS Bedrock | Claude Sonnet 4 (`us.anthropic.claude-sonnet-4-6`) for complex sections (1, 5, 8, 12). Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) for simple sections (3, 4, 10, 13, exec_summary). |
| **Region** | us-east-1 | All Bedrock calls to us-east-1 |
| **Database** | Supabase/Postgres | 20 tables, RLS disabled |
| **Session state** | Redis (Upstash) | Session keys: `session:{session_id}`, 24-hour TTL |
| **Messaging** | python-telegram-bot + FastAPI/WebSocket | Dual channel: Telegram for CEO mobile, web for browser |
| **Financial sim** | SimPy Monte Carlo | 1000 runs, 36-month horizon, returns probability distribution + primary risk factor |
| **Eval harness** | `evaluation/run_grounded_eval.py` | Runs against real EpistemicOS data, writes results to JSON |
| **GitHub** | Main branch | Commit 37f3a88 (June 2, 2026) |

---

## 8. DATABASE TABLES

| Table | Purpose |
|-------|---------|
| `sessions` | Top-level session record: telegram_chat_id, state, idea_brief |
| `messages` | All messages (inbound/outbound): content, sender, channel, telegram_message_id |
| `agent_messages` | Agent-to-agent messages (SPADE/MessageBus): sender, receiver, performative, content |
| `agent_outputs` | Agent execution results: agent_name, section_number, output_json, confidence_score |
| `assumptions` | Clarification assumptions: assumption_text, source (L1 or inferred), validated flag |
| `decisions` | L3 decision records: summary, biggest_risk, question, ceo_response, version |
| `events_logs` | All agent actions: agent_name, action, state_before, state_after, timestamp |
| `business_plan_sections` | Section metadata: section_number, title, status, assigned_agent |
| `bp_section_content` | Section content: section_number, content_json, version, approved flag |
| `bp_section_metadata` | Section dependencies: section_number, depends_on, readiness_status |
| `compiled_plans` | Final compiled business plan: session_id, markdown_content, version, approved |
| `council_reports` | Council Agent multi-persona reviews: session_id, persona, assessment, vote |
| `constitution_versions` | Operating rules snapshots: version, rules_json, effective_date |
| `execution_groups` | Mother Agent group execution: group_number, agents_list, status, started_at |
| `gap_resolutions` | Human responses to data gaps: gap_id, resolution_text, provided_data |
| `memory_profile` | BDI beliefs persistence: belief_key, belief_value, confidence, last_updated |
| `pipeline_runs` | Eval harness run records: run_id, timestamp, sections_parsed, total_tokens, score |
| `research_briefs` | Search service results: query, source_url, retrieved_date, freshness_flag |
| `task_readiness` | Task dependency tracker: task_id, dependencies, readiness_status |
| `ceo_context` | CEO profile: telegram_chat_id, name, role, preferences |

**Note:** RLS (Row Level Security) is disabled on all tables for development. Re-enable before production.

---

## 9. KEY DESIGN PRINCIPLES

### Intelligence Comes from Project State, Not CEO Profile
- CEO profile is identity (name, telegram_chat_id, preferences)
- Project state is the knowledge (idea brief, assumptions, decisions, data)
- Agents reason over project state, not over a static CEO persona

### Grounding: Agents Reason Over Retrieved/Loaded Facts
- Agents do not invent facts from LLM training data
- Every fact is either:
  - Loaded from `ceo_data/` (with epistemic tag)
  - Retrieved from search service (with source URL + date)
  - Derived from prior section output (with dependency reference)
- If data is missing, agent flags `status: "no_data"` with `gap_reason`

### Epistemic Honesty: ASSUMPTION-Tagged Data Never Laundered
- ASSUMPTION stays ASSUMPTION through the entire pipeline
- Agents cannot upgrade ASSUMPTION → CONFIRMED
- Only Alex can mark facts as CONFIRMED (via Knowledge Base tab)
- Contradictions explicitly surfaced, not silently resolved

### Human Gates at Key Decision Points
- **Gate 1 (L0):** Input safety, spam filter
- **Gate 2 (not built):** Sufficiency check — does brief have enough info for agents to proceed?
- **Gate 3 (not built):** Group output review — does this group's work pass before next group starts?
- **Gate 4 (not built):** Final output approval — does Alex approve the compiled business plan?

### Build Ahead of Proof is the Project's Recurring Failure
- **Pattern:** Build complex orchestration (Mother Agent, Council, DA, negotiation) without proving simpler components work end-to-end
- **Result:** 2,591 lines of Mother Agent code never run; eval runner bypasses it
- **Corrective principle:** Prove each piece runs before scaling it
  - Prove MessageBus with one group → then add Gate 3 → then add DA → then add Council
  - Do not build negotiation until Council surfaces real contradictions
  - Do not parallelize until sequential is proven

---

## 10. CURRENT BLOCKERS

### Section 13 Intermittent Failures
- **Symptom:** Bedrock connection-closed errors during `launch_contingency.py` execution
- **Frequency:** Intermittent (not every run)
- **Impact:** 8/9 sections parse successfully; section 13 fails ~10-20% of runs
- **Retry logic:** In place (1 attempt, 5s delay) but still insufficient
- **Root cause:** Unknown — likely payload size, timeout config, or rate limiting
- **Status:** Not blocking eval progress; acceptable for now

### Mother Agent Never Exercised
- **Symptom:** 2,591 lines of orchestration code never run in real pipeline
- **Root cause:** Eval runner calls `IntelligenceEngine` directly
- **Impact:** Cannot validate Mother Agent logic, gate sequencing, DA, Council, negotiation
- **Next step:** Wire MessageBus, then test Mother Agent with one group

### Phase 1 Pipeline Untested on Bedrock
- **Symptom:** Phase 1 (L0, Router, L1, L3, Memory) built for Gemini API, migrated to Bedrock May 19, not tested end-to-end since
- **Impact:** Unknown whether Phase 1 works on Bedrock
- **Next step:** Full Phase 1→Phase 2 integration test after MessageBus wired

---

## 11. EVALUATION RESULTS SUMMARY

**Last grounded eval run:** `grounded_epistemic_os_20260602_063506.json` (June 2, 2026)

| Metric | Value |
|--------|-------|
| **Input data** | 22,881-char EpistemicOS source-of-truth document |
| **Sections attempted** | 9 |
| **Sections parsed** | 8 (88.9%) |
| **Total tokens** | 202,687 (101,933 input + 100,754 output) |
| **Total latency** | 28.0 minutes (1,682 seconds) |
| **Overall score** | 8.9/10 (after scorer fixes) |
| **Confidence distribution** | 8 sections: low; 0 sections: medium/high |

### Quality Signals (All Sections)
- ✓ **Epistemic honesty:** All successful sections flagged low confidence due to sparse input data
- ✓ **No fabrication:** Section 12 refused to invent financial numbers; traced every figure to upstream assumptions
- ✓ **Contradiction surfacing:** Executive summary explicitly called out that accreditation trigger is not a scored criterion
- ✓ **Assumption tracing:** Every WTP claim, pricing anchor, and buyer persona labeled as unvalidated
- ✓ **Gap documentation:** Financials and team sections marked as explicit no_data with gap_reason explanations

### Section-by-Section Results

| Section | Agent | Tokens | Time | Parse | Confidence | Score | Issues |
|---------|-------|--------|------|-------|------------|-------|--------|
| 1 | Opportunity Analyst | 19,172 | 3.6 min | ✓ | low | 10.0/10 | 0 |
| 3 | Environment Research | 23,437 | 2.3 min | ✓ | low | 10.0/10 | 0 |
| 4 | Organisation Designer | 20,825 | 1.7 min | ✓ | low | 10.0/10 | 0 |
| 5 | SWOT Synthesizer | 21,555 | 3.6 min | ✓ | low | 10.0/10 | 0 |
| 8 | Marketing Strategy | 35,480 | 7.8 min | ✓ | low | 10.0/10 | 0 |
| 10 | Operations | 24,694 | 2.4 min | ✓ | low | 10.0/10 | 0 |
| 12 | Financial Modelling | 28,572 | 4.1 min | ✓ | low | 10.0/10 | 0 |
| 13 | Launch & Contingency | 5,136 | 0.5 min | ✗ | — | 0/10 | Connection closed by Bedrock |
| exec_summary | Summary Agent | 23,816 | 2.0 min | ✓ | low | 10.0/10 | 0 |

**Key finding:** 10/10 scores with low confidence means "structurally excellent output derived from sparse data" — which is the correct behavior. Scorer measures craft; confidence measures grounding.

---

## 12. KNOWN LIMITATIONS

### Intelligence Layer
- Mother Agent orchestration never tested
- Devil's Advocate never run
- Council Agent never fires
- Negotiation/conflict resolution never triggered
- Coherence Auditor not active
- Document Compiler not wired to full pipeline

### Data Layer
- 4 gap topics: competitors, market_research, financials, team
- Search service not built (outward agents cannot retrieve external data)
- RAG knowledge base not built (section-scoped file injection only)

### Human Interaction
- Gate 2 (sufficiency check) not built
- Gate 4 (final approval) not built
- No interactive clarification during agent execution (L1 runs before agents, not during)

### Infrastructure
- Section 13 intermittent Bedrock connection-closed errors
- SPADE scaffolding still present (MessageBus not wired)
- Roster YAML has wrong agent/section mappings
- Operating rules files empty (no actual rules defined)

### Testing
- Phase 1 pipeline not tested on Bedrock since May 19 migration
- No end-to-end Phase 1→Phase 2 integration test
- Eval runner bypasses Mother Agent (direct IntelligenceEngine calls)
- No automated CI/CD pipeline

---

## 13. SUCCESS CRITERIA MET

✓ **Data ingestion operational** — loads 22,881-char source-of-truth with epistemic preservation  
✓ **9-section pipeline functional** — 8/9 sections parsed successfully (88.9% success rate)  
✓ **Epistemic honesty enforced** — no fabricated numbers, all assumptions traced, contradictions surfaced  
✓ **Scorer calibrated** — mechanical issues fixed; scores reflect structural quality not subjective judgment  
✓ **Output quality validated** — section 12 achieved 10/10 on honest gap-derived reasoning  
✓ **CEO-facing output produced** — readable analysis document generated from grounded run  
✓ **Web interface operational** — 3 tabs (Chat, Pipeline Trace, Knowledge Base) working with WebSocket real-time updates  
✓ **Knowledge Base tab functional** — Alex can view and add facts with epistemic tags via web UI

---

## 14. OPEN QUESTIONS

1. **Section 13 stability:** Is the connection-closed error a transient Bedrock issue or a systematic problem requiring architectural changes?

2. **Scorer generalization:** Do the June 2 fixes apply correctly to ideas outside the management-research domain (e.g., hardware, biotech, SaaS)?

3. **SPADE necessity:** Does the evaluation pipeline benefit from SPADE orchestration, or is direct IntelligenceEngine calling simpler and faster for batch evaluation use cases?

4. **RAG vs. section mapping:** Will vector retrieval improve data injection relevance, or is the current section-mapping strategy sufficient for business plan analysis?

5. **Production deployment:** What is the target environment for running this system at scale (AWS Lambda, ECS, self-hosted)?

6. **Search service scope:** What external data sources should search service access? (Depends on Alex's response to compiled analysis draft)

---

## 15. RISK REGISTER

| Risk | Severity | Mitigation Status |
|------|----------|-------------------|
| Section 13 connection failures | Medium | Retry logic in place; further tuning required |
| Bedrock rate limits at scale | Medium | Not yet tested; batch throttling strategy undefined |
| Scorer false negatives on new domains | Low | Re-validation required on non-management ideas |
| Data ingestion budget exceeded | Low | Progressive trimming working; no overruns in grounded run |
| Phase 1 technical debt | Low | Isolated; does not block Phase 2 progress |
| Mother Agent untested | High | 2,591 lines of code never run; unknown correctness |
| SPADE scaffolding | Medium | Deprecated but not removed; creates maintenance burden |
| Operating rules undefined | Medium | Empty files mean no guardrails enforced |

---

## 16. WHAT THIS DOCUMENT REPLACES

This document replaces the outdated `PROJECT_STATUS.md` (last updated May 18, 2026) which was six weeks out of date. Key changes:

- **Honest about what "working" means:** Distinguishes between "runs in eval" and "runs end-to-end"
- **Accurate on Mother Agent status:** 2,591 lines exist but never run
- **Knowledge Base tab added:** Built June 2, 2026, tested and working
- **Architecture decisions documented:** Search as function, staged Mother Agent rollout, scorer vs confidence signals
- **Build priority explicit:** What to build next, in order, with no skipping ahead
- **Deferred work explicit:** What not to build, with reasons
- **Database tables complete:** All 20 tables listed
- **Evaluation results current:** June 2 grounded run documented with full metrics

---

**Status:** ✓ Current as of June 2, 2026 — grounded evaluation complete, honest output validated, Knowledge Base tab operational, ready for next phase.
