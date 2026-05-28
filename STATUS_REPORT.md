# Multi-Agent System — Status Report

**Last updated:** 2026-05-28  
**Phase:** 2 (Active)  
**Branch:** main

---

## What Has Been Done

### Phase 1 (Complete)
- L0/L1/L3 agent pipeline with state machine
- Telegram integration for CEO (Alex) communication
- Supabase persistence + Redis session memory
- Gate 1 approval flow

### Phase 2 — Core Agents (Complete)
All 15 agents built, tested, and wired into the pipeline:

| # | Agent | Role | Model | Status |
|---|-------|------|-------|--------|
| 1 | Mother Agent | Orchestrator — plans, routes, validates | Sonnet | Done |
| 2 | Opportunity Analyst | Section 1: Opportunity, ICP, objectives | Sonnet | Done |
| 3 | Environment Research | Section 3: PEST, Five Forces, risks | Haiku | Done |
| 4 | Organisation Designer | Section 4/11: Org structure, HR plan | Haiku | Done |
| 5 | SWOT Synthesizer | Section 5: SWOT matrix synthesis | Sonnet | Done |
| 6 | Marketing Strategy | Section 7/8/9: Full marketing plan | Sonnet | Done |
| 7 | Operations | Section 6/10: Production, operations | Haiku | Done |
| 8 | Financial Modelling | Section 12: 3-statement, SimPy Monte Carlo | Sonnet | Done |
| 9 | Launch & Contingency | Section 13/14: Launch programme, contingency | Haiku | Done |
| 10 | Summary Agent | Executive summary | Haiku | Done |
| 11 | Council Agent | 5-persona quality gate (SWOT, Marketing, Financial, Summary) | Haiku+Sonnet | Done |
| 12 | Devil's Advocate | Adversarial review of all outputs | Sonnet | Done |
| 13 | Intelligence Engine | 4-step reasoning (decompose/produce/challenge/revise) | Sonnet | Done |
| 14 | Learning Engine | CEO feedback memory, DA accuracy tracking | — | Done |
| 15 | Document Compiler | Final business plan assembly | Sonnet | Done |

### Phase 2 — Infrastructure (Complete)
- SPADE messaging protocol (XMPP) with ACL performatives
- 4 execution groups with dependency ordering
- Gate 2 approval (Alex reviews before each group executes)
- Pipeline resume from last completed group on failure
- Fault-tolerant execution (one agent crash doesn't kill pipeline)
- SimPy Monte Carlo simulation (1000 runs, P10/P50/P90)
- Constitution v2 enforcement
- Sequential pipeline with `dependency_map.yaml`

### Phase 2 — Quality Stack (Complete)
- Intelligence Engine 4-step reasoning on all outputs
- Devil's Advocate adversarial review with revision loop
- Council Agent 5-persona deliberation gate (max 2 revisions)
- Coherence Audit (cross-section contradiction detection)
- Learning Engine (CEO feedback memory)
- Evidence grading on assumptions
- So-What Filter (does output help Alex decide?)
- Hard constraint propagation (numerical consistency)
- Confidence ceiling (downstream can't exceed upstream)
- Backward passes (downstream findings revise upstream)
- Hypothesis testing (funnel math validation)
- Uncertainty propagation across sections
- CEO data injection layer

### Phase 2 — Frontend (Complete)
- Streamlit app with live pipeline trace UI
- Evaluation dashboard (4 tabs: Overview, Run Detail, Compare, Costs)

### Phase 2 — Testing (Complete)
- **End-to-end test suite:** `tests/test_full_pipeline_e2e.py` — 14 tests covering:
  - Full pipeline orchestration (all 4 groups, 9 agents, Gate 2, coherence audit, delivery)
  - Gate 2 kill/edit flows
  - Schema validation for all 9 agent outputs
  - Cross-section context propagation
  - Confidence ceiling enforcement
  - Hard constraint propagation
  - Fallback output on timeout
  - Coherence audit with issue detection
  - Assumption deduplication
  - Pipeline resume from checkpoint
  - Council-gated section routing
  - Execution group dependency ordering
  - Data flow integrity (revenue, ICP, SWOT consistency)
- **Unit tests:** 33 council tests, routing tests, notification tests
- **Existing e2e tests:** 12 tests for individual agent parse/fallback logic
- **All tests pass.**

### Phase 2 — Evaluation Harness (Complete)
- `evaluation/eval_runner.py` — feeds test ideas through Intelligence Engine
- `evaluation/scorer.py` — scores output on schema compliance, specificity, completeness
- `evaluation/test_ideas.py` — 5 diverse test business ideas
- Streamlit evaluation dashboard

---

## First Eval Baseline Results (2026-05-28)

**Test idea:** AI-Powered CRM for Freelancers  
**Model:** Claude Sonnet 4.6 (via AWS Bedrock)  
**Reasoning:** 4-step Intelligence Engine (decompose/produce/challenge/revise)

| Section | Agent | Score | Status | Latency | Tokens |
|---------|-------|-------|--------|---------|--------|
| 1 | Opportunity Analyst | 9.6/10 | Pass | 222s | 18,395 |
| 3 | Environment Research | 10.0/10 | Pass | 158s | 24,857 |
| 5 | SWOT Synthesizer | 0/10 | Timeout | 1,105s | 4,112 |
| 8 | Marketing Strategy | 0/10 | Timeout | 1,101s | 3,406 |
| 12 | Financial Modelling | 8.0/10 | Pass | 526s | 26,990 |
| 13 | Launch & Contingency | 0/10 | Parse fail | 183s | 31,697 |

**Overall:** 3/6 sections pass consistently (50% schema compliance)  
**Total tokens:** 109,457  
**Total latency:** 55 minutes

### Key Findings
1. **Sections 1, 3, 12 produce excellent output** (8-10/10) with rich, specific, actionable content
2. **Timeouts are intermittent** — the very first run had ALL 6 sections pass before a scorer bug crashed it. Bedrock throttling under load causes 5 and 8 to fail.
3. **Section 13 REVISE step** produces unparseable output on Haiku (format drift on contingency plans)
4. **Intelligence Engine reasoning quality is high** but adds significant latency (150-540s per section)

### Baseline Data Files
- `evaluation/results/baseline_v1.json` — structured baseline summary
- `evaluation/results/eval_run_20260528_094310.json` — full 6-section run data
- `evaluation/results/eval_run_20260528_094831.json` — 4-section run (skipped 5, 8)

---

## Bugs Fixed (2026-05-28)

1. **`mother_agent.py:1480`** — `_deliver_plan()` called without `await`. The final delivery coroutine was silently discarded after a clean coherence audit. Fixed: added `await`.
2. **`evaluation/scorer.py:179`** — `confidence_score` returned as dict from LLM crashed the scorer (`TypeError: unhashable type`). Fixed: added `isinstance(conf, str)` guard.

---

## What Needs To Be Done

### Immediate (Action Items from Baseline)
- [ ] Increase Bedrock `read_timeout` from 120s to 180s for Sonnet sections (5, 8)
- [ ] Fix Section 13 REVISE parsing — fallback to PRODUCE output when REVISE fails
- [ ] Re-run full eval baseline (all 5 test ideas) during off-peak hours

### Pending
- [ ] RAG knowledge base (blocked — waiting on Alex's existing data: financials, customer research, decks)

### Deferred (Post-Stability)
- [ ] Best-of-N sampling with DA as scorer (after pipeline works e2e consistently)
- [ ] DSPy prompt optimization (needs 20+ runs with Alex feedback)

---

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
python tests/test_full_pipeline_e2e.py   # Full pipeline e2e (14 tests)
python tests/test_phase2_e2e.py          # Agent-level e2e (12 tests)
pytest tests/                            # All test files

# Run evaluation
python evaluation/eval_runner.py                       # All 5 ideas, all sections
python evaluation/eval_runner.py --idea eval_saas_crm  # Single idea
python evaluation/eval_runner.py --section 1,3,12      # Specific sections

# Start the system
python main.py                          # Full pipeline (Phase 1 + Phase 2)
streamlit run app.py                    # Internal monitoring UI
python telegram/webhook.py              # Telegram listener
```

---

## Architecture Summary

```
Telegram (Alex) → Phase 1 (L0/L1/L3) → Gate 1 Approval
                                              ↓
                                    Phase 2 Pipeline Trigger
                                              ↓
                                    Mother Agent (Orchestrator)
                                              ↓
                    ┌─────────────────────────────────────────────┐
                    │  Group 1: Opportunity Analyst + Org Designer │ (parallel)
                    │  Group 2: Environment + Marketing            │ (parallel)
                    │  Group 3: SWOT → Marketing → Operations     │ (sequential)
                    │  Group 4: Financial → Launch → Summary       │ (sequential)
                    └─────────────────────────────────────────────┘
                                              ↓
                    Each group: Gate 2 (Alex) → Execute → DA Review
                    Council-gated (5, 8, 12, summary): + 5-persona review
                                              ↓
                    Coherence Audit → Document Compilation → Delivery
```
