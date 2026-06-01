# Multi-Agent Business Plan System — Project Status

Last updated: 2026-06-01

---

## Current State

**Phase 2 is feature-complete for the evaluation path.** The system produces a 9-section business plan end-to-end through the eval runner (bypassing SPADE), with output-length caps preventing truncation and all sections parsing successfully.

---

## What Has Been Built

### Phase 1 (Complete — before May 19, 2026)
- L0/L1/L3 pipeline: intake, clarification, decision gate
- Telegram integration for CEO (Alex) interaction
- Supabase canonical DB, Redis session memory
- Streamlit internal monitoring UI
- Gemini LLM integration (deprecated in Phase 2)

### Phase 2 (Active — May 19 onward)

#### Core Infrastructure
- **Intelligence Engine** (`agents/phase2/intelligence_engine.py`) — multi-step reasoning with judgment checks, re-production on gaps, cross-section context injection
- **Learning Engine** (`agents/phase2/learning_engine.py`) — pattern extraction and feedback loops
- **Council Agent** (`agents/phase2/council_agent.py`) — 5-persona deliberation gate for quality review
- **Devil's Advocate** (`agents/phase2/devils_advocate.py`) — adversarial challenge agent
- **Mother Agent** (`agents/phase2/mother_agent.py`) — full orchestrator with dependency map, execution groups, backward passes, hypothesis testing
- **Message Bus** (`agents/phase2/message_bus.py`) — SPADE XMPP messaging layer
- **Negotiation** (`agents/phase2/negotiation.py`) — inter-agent proposal/counter-proposal
- **Document Compiler** (`agents/phase2/document_compiler.py`) — final document assembly

#### Child Agents (Section Producers)
| Section | Agent | File | Model | Status |
|---------|-------|------|-------|--------|
| 1 | Opportunity Analyst | `opportunity_analyst.py` | Sonnet | Working |
| 3 | Environment Research | `environment_research.py` | Haiku | Working |
| 4 | Organisation Designer | `organisation_designer.py` | Haiku | Working |
| 5 | SWOT Synthesizer | `swot_synthesizer.py` | Sonnet | Working |
| 8 | Marketing Strategy | `marketing_strategy.py` | Sonnet | Working |
| 10 | Operations | `operations.py` | Haiku | Working |
| 12 | Financial Modelling | `financial_modelling.py` | Sonnet | Working |
| 13 | Launch & Contingency | `launch_contingency.py` | Haiku | Working |
| exec | Summary Agent | `summary_agent.py` | Haiku | Working |

#### Sections NOT Implemented (Stubs Only)
| Section | Name | Reason |
|---------|------|--------|
| 2 | Entrepreneur & development team | `always_required: false`, no code |
| 6 | R&D and technology | `always_required: false`, no code |
| 7 | Agreements, alliances, outsourcing | `always_required: false`, no code |
| 9 | Quality management | `always_required: false`, no code |
| 11 | Human resources plan | `always_required: true` but no code |
| 14 | Contingency plan | Folded into section 13 output |

#### Evaluation Harness
- **Eval Runner** (`evaluation/eval_runner.py`) — runs all 9 sections sequentially via Intelligence Engine, bypassing SPADE
- **Scorer** (`evaluation/scorer.py`) — schema compliance, confidence scoring, LLM judge
- **Test Ideas** (`evaluation/test_ideas.py`) — 5 test business ideas (SaaS CRM, coffee subscription, AI consultancy, IoT irrigation, tutoring marketplace)

---

## Key Fixes Applied (This Session — 2026-06-01)

### 1. Section 12 (Financial Modelling) Truncation Fix
- **Problem:** 20,082 output tokens, 381s latency. `risk_factors` and `confidence_score` dropped due to truncation.
- **Fix:** Added OUTPUT LENGTH CONSTRAINTS block to prompt. Capped pl_monthly_year1 to 12 rows, assumption_log to 8 items, total target < 4000 tokens. Listed REQUIRED FIELDS at top of output order.
- **Result:** 10,628 output tokens, 227s latency. All fields present.

### 2. Section 13 (Launch & Contingency) Truncation Fix
- **Same pattern as section 12.** Applied before this session (commit `b32d4c5`).

### 3. Sections 4, 10, Executive Summary — Wired into Eval Runner
- **Problem:** Only 6 of 14 sections were in the eval runner. Missing sections had real agent code but weren't connected.
- **Fix:** Added AGENT_CONFIGS entries, output-length caps, input data builders, explicit execution order.
- **Result:** 9 sections now run end-to-end. All parse. All under 16K output tokens.

---

## Latest Eval Run Results

**File:** `evaluation/results/eval_run_20260601_091012.json`
**Date:** 2026-06-01
**Idea:** AI-Powered CRM for Freelancers
**Sections:** 9 (all available)

| Section | Agent | Latency | Out Tokens | Parsed | Score |
|---------|-------|---------|-----------|--------|-------|
| 1 | Opportunity Analyst | 214.0s | 9,171 | yes | 9.6/10 |
| 3 | Environment Research | 164.2s | 15,636 | yes | 9.6/10 |
| 4 | Organisation Designer | 126.0s | 12,013 | yes | 6.7/10 |
| 5 | SWOT Synthesizer | 310.1s | 14,897 | yes | 9.5/10 |
| 8 | Marketing Strategy | 334.5s | 14,580 | yes | 10.0/10 |
| 10 | Operations | 143.6s | 14,743 | yes | 6.7/10 |
| 12 | Financial Modelling | 227.3s | 11,417 | yes | 5.0/10 |
| 13 | Launch & Contingency | 157.6s | 14,890 | yes | 9.7/10 |
| exec | Executive Summary | 121.4s | 10,737 | yes | 2.6/10 |

**Totals:** 30 min wall-clock, 224K tokens, 0 errors, 100% schema compliance, 7.7/10 overall.

---

## Known Issues / Next Steps

### Bugs to Fix
1. **Section 8 confidence_score returns float (0.41) instead of enum ("high"|"medium"|"low")** — Intelligence Engine judgment pipeline issue, not a parsing failure.
2. **Section 12 scorer gives 5.0/10** — penalizes `confidence_score: "low"`. This is correct model behavior (honest about speculative assumptions) but the scorer doesn't distinguish "deliberately low confidence" from "broken output."
3. **Executive Summary scorer gives 2.6/10** — likely the scorer expects fields or lengths that differ from the new capped output. Needs scorer calibration.

### Missing Implementations
4. **Section 11 (HR Plan)** — `always_required: true` but no agent code. Organisation Designer owns it in the roster but only produces section 4. Needs implementation.
5. **Sections 2, 6, 7, 9** — conditional sections, not always produced. Low priority but needed for completeness.

### Performance
6. **30 min for 9 sections is slow.** Sections run sequentially. Sections 1+3+4 could run in parallel (no dependencies between them). Section 5 waits for 3+4. Sections 8+10 could parallel after 5. This is a separate optimization — do NOT add parallelism until correctness is solid.
7. **All confidence_scores are "low"** — every section reports low confidence because the test ideas provide minimal CEO input. This is correct behavior but means the scorer penalizes heavily. Consider a "data-rich" test idea with full CEO assumptions to test high-confidence paths.

### Quality
8. **Scorer calibration** — sections 4, 10, exec_summary score low despite parsing perfectly. The scorer may be checking for fields or patterns that no longer match the capped output format. Review `evaluation/scorer.py`.
9. **Intelligence Engine re-production** — multiple sections triggered "Draft missing N judgments — re-producing with gaps." This adds latency (effectively 2x LLM calls). Investigate whether the judgment prompts are too strict.

### Integration (Not Started)
10. **SPADE full pipeline test** — the eval runner bypasses SPADE/XMPP. The Mother Agent orchestration path has not been tested with these fixes. Needs a real SPADE run.
11. **Telegram end-to-end** — Alex sends idea via Telegram -> full pipeline -> executive summary back to Alex. Not tested since Phase 1.
12. **RAG Knowledge Base** — Alex's existing data (financials, customer research, decks) was requested but never provided. Blocks "validated" assumption labels.

---

## File Structure Quick Reference

```
agents/phase2/          # All Phase 2 agent code
  mother_agent.py       # Orchestrator (121K, largest file)
  intelligence_engine.py # Multi-step reasoning engine
  [agent_name].py       # One file per child agent
config/phase2/
  agent_roster.yaml     # Agent registry, execution groups
  dependency_map.yaml   # Section dependencies, inputs/outputs
evaluation/
  eval_runner.py        # Evaluation harness
  scorer.py             # Output scoring
  test_ideas.py         # Test business ideas
  results/              # JSON output from eval runs
schemas/
  inputs/               # Pydantic input schemas per agent
  outputs/              # Pydantic output schemas per agent
simulation/
  financial_sim.py      # SimPy Monte Carlo simulation
```

---

## How to Run

```bash
# Full eval (all 9 sections, one idea)
python evaluation/eval_runner.py --idea eval_saas_crm

# Single section only
python evaluation/eval_runner.py --idea eval_saas_crm --section 12

# All 5 test ideas (expensive — ~2.5 hours, ~1M tokens)
python evaluation/eval_runner.py

# Tests
pytest tests/

# Streamlit dashboard
streamlit run app.py
```
