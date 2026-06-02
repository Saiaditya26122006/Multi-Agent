# Multi-Agent System — Visual Architecture & Status Report

**Generated:** 2026-06-01
**Phase:** 2 (Active)

---

## 1. SYSTEM ARCHITECTURE — FULL VISUAL

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                            CEO (Alex) — TELEGRAM                                     │
│                         Sends business idea / answers                                 │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 — INTAKE PIPELINE                                    │
│                                                                                      │
│  ┌──────────┐     ┌────────────────┐     ┌────────────────┐     ┌──────────────┐   │
│  │ L0 Input │────▶│ Router Agent   │────▶│ L1 Clarity    │────▶│ L3 Feedback  │   │
│  │ Guard    │     │ (classify msg) │     │ Agent (Q&A)   │     │ Agent (gate) │   │
│  └──────────┘     └────────────────┘     └────────────────┘     └──────────────┘   │
│       │                                         │                       │            │
│       │ validates                               │ asks 1-3             │ presents   │
│       │ message                                 │ questions            │ decision   │
│       │                                         │ via Telegram         │ Yes/Adj/Kill│
│                                                                                      │
│  ┌──────────────┐                                                                    │
│  │ Memory Agent │ ← consolidates sessions, generates welcome-back                    │
│  └──────────────┘                                                                    │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │ Approved idea + CEO assumptions
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 2 — BUSINESS PLAN GENERATION                           │
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │                      MOTHER AGENT (Orchestrator)                              │    │
│  │                                                                               │    │
│  │  • Dependency resolution (YAML map)                                           │    │
│  │  • Execution group sequencing                                                 │    │
│  │  • Backward pass coherence audit                                              │    │
│  │  • Contradiction detection & negotiation                                      │    │
│  │  • Pipeline checkpoints (early kill)                                          │    │
│  │  • Quality gate routing (DA / Council)                                        │    │
│  │  • Hypothesis testing                                                         │    │
│  │  • CEO escalation (only on deadlock)                                          │    │
│  └─────────────────────────────────┬───────────────────────────────────────────┘    │
│                                    │                                                  │
│            ┌───────────────────────┼───────────────────────┐                         │
│            ▼                       ▼                       ▼                          │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐                  │
│  │ Intelligence    │   │ Learning Engine  │   │ Message Bus      │                  │
│  │ Engine (IE)     │   │                  │   │ (SPADE/Async)    │                  │
│  │                 │   │ • Pattern extract│   │                  │                  │
│  │ 4-step chain:   │   │ • CEO prefs     │   │ • ACL messages   │                  │
│  │ DECOMPOSE →     │   │ • Prompt adapt  │   │ • Performatives  │                  │
│  │ PRODUCE →       │   │ • Run-over-run  │   │ • Supabase log   │                  │
│  │ CHALLENGE →     │   │   improvement   │   │                  │                  │
│  │ REVISE          │   │                  │   │                  │                  │
│  └────────┬────────┘   └──────────────────┘   └──────────────────┘                  │
│           │                                                                           │
│           │ Powers every child agent's reasoning                                      │
│           ▼                                                                           │
│  ┌────────────────────────────────────────────────────────────────────────────┐      │
│  │                    CHILD AGENTS (Section Producers)                          │      │
│  │                                                                              │      │
│  │  EXECUTION GROUP 1 (parallel — no dependencies between them):                │      │
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────────┐   │      │
│  │  │ §1 Opportunity   │  │ §3 Environment     │  │ §4 Organisation        │   │      │
│  │  │ Analyst [Sonnet] │  │ Research [Haiku]   │  │ Designer [Haiku]       │   │      │
│  │  └────────┬─────────┘  └─────────┬──────────┘  └───────────┬────────────┘   │      │
│  │           │                      │                          │                │      │
│  │           ▼                      ▼                          ▼                │      │
│  │  EXECUTION GROUP 2 (depends on §3 + §4):                                    │      │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │      │
│  │  │ §5 SWOT Synthesizer [Sonnet] — merges PEST + Five Forces + Org      │   │      │
│  │  └──────────────────────────────────┬───────────────────────────────────┘   │      │
│  │                                     │                                        │      │
│  │                                     ▼                                        │      │
│  │  EXECUTION GROUP 3 (depends on §5):                                          │      │
│  │  ┌──────────────────────┐  ┌────────────────────┐                           │      │
│  │  │ §8 Marketing         │  │ §10 Operations     │                           │      │
│  │  │ Strategy [Sonnet]    │  │ [Haiku]            │                           │      │
│  │  └─────────┬────────────┘  └─────────┬──────────┘                           │      │
│  │            │                          │                                      │      │
│  │            ▼                          ▼                                      │      │
│  │  EXECUTION GROUP 4 (depends on §8 + §10):                                   │      │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │      │
│  │  │ §12 Financial Modelling [Sonnet] + SimPy Monte Carlo (1000 runs)    │   │      │
│  │  └──────────────────────────────────┬───────────────────────────────────┘   │      │
│  │                                     │                                        │      │
│  │                                     ▼                                        │      │
│  │  EXECUTION GROUP 5 (depends on §12):                                         │      │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │      │
│  │  │ §13 Launch & Contingency [Haiku]                                     │   │      │
│  │  └──────────────────────────────────┬───────────────────────────────────┘   │      │
│  │                                     │                                        │      │
│  │                                     ▼                                        │      │
│  │  FINAL:                                                                      │      │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │      │
│  │  │ Executive Summary Agent [Haiku] — reads ALL prior sections           │   │      │
│  │  └──────────────────────────────────────────────────────────────────────┘   │      │
│  └──────────────────────────────────────────────────────────────────────────────┘      │
│                                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────────┐       │
│  │                    QUALITY & REVIEW LAYER                                   │       │
│  │                                                                              │       │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────┐     │       │
│  │  │ Devil's Advocate │  │ Council Agent    │  │ Coherence Auditor     │     │       │
│  │  │                  │  │ (5 personas)     │  │                       │     │       │
│  │  │ Challenges every │  │                  │  │ Cross-section checks: │     │       │
│  │  │ section output   │  │ • Skeptic        │  │ • Revenue match       │     │       │
│  │  │ for:             │  │ • Architect      │  │ • ICP consistency     │     │       │
│  │  │ • Overconfidence │  │ • Visionary      │  │ • Headcount vs costs  │     │       │
│  │  │ • Logical gaps   │  │ • Stranger       │  │ • Timeline alignment  │     │       │
│  │  │ • Math errors    │  │ • Operator       │  │ • Confidence chains   │     │       │
│  │  │ • Contradictions │  │                  │  │                       │     │       │
│  │  │ • Survivor bias  │  │ Synthesizes a    │  │                       │     │       │
│  │  │                  │  │ pass/revise/kill │  │                       │     │       │
│  │  │ Verdict:         │  │ verdict          │  │                       │     │       │
│  │  │ pass/revise/     │  │                  │  │                       │     │       │
│  │  │ reject           │  │                  │  │                       │     │       │
│  │  └──────────────────┘  └──────────────────┘  └───────────────────────┘     │       │
│  └────────────────────────────────────────────────────────────────────────────┘       │
│                                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────────┐       │
│  │                    CONFLICT RESOLUTION LAYER                                │       │
│  │                                                                              │       │
│  │  ┌──────────────────────┐  ┌──────────────────────────────────────────┐    │       │
│  │  │ Negotiation Manager  │  │ Conflict Resolver                         │    │       │
│  │  │                      │  │                                            │    │       │
│  │  │ • Max 3 rounds       │  │ • Prioritizes contradictions by severity  │    │       │
│  │  │ • Propose/counter    │  │ • Routes to negotiation first             │    │       │
│  │  │ • Evidence-based     │  │ • Formats escalation only on deadlock     │    │       │
│  │  │ • Consensus/         │  │                                            │    │       │
│  │  │   compromise/        │  │                                            │    │       │
│  │  │   deadlock           │  │                                            │    │       │
│  │  └──────────────────────┘  └──────────────────────────────────────────┘    │       │
│  └────────────────────────────────────────────────────────────────────────────┘       │
│                                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────────┐       │
│  │                    FINAL OUTPUT                                              │       │
│  │                                                                              │       │
│  │  ┌──────────────────────────────────────────────────────────────────────┐  │       │
│  │  │ Document Compiler — JSON sections → cohesive Markdown business plan  │  │       │
│  │  └──────────────────────────────────────────────────────────────────────┘  │       │
│  └────────────────────────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         INFRASTRUCTURE LAYER                                          │
│                                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ Supabase     │  │ Redis        │  │ AWS Bedrock    │  │ SimPy Monte Carlo    │  │
│  │ (Postgres)   │  │ (Upstash)    │  │ (Claude LLM)   │  │ (Financial Sim)      │  │
│  │              │  │              │  │                │  │                      │  │
│  │ • Sessions   │  │ • Session    │  │ • Sonnet 4     │  │ • 1000 runs          │  │
│  │ • Decisions  │  │   state      │  │ • Haiku 4.5    │  │ • 36-month horizon   │  │
│  │ • Agent msgs │  │ • Beliefs    │  │                │  │ • P10/P50/P90        │  │
│  │ • Events log │  │ • Patterns   │  │                │  │ • Break-even month   │  │
│  │ • Profiles   │  │ • Learning   │  │                │  │ • Cash-out risk      │  │
│  └──────────────┘  └──────────────┘  └────────────────┘  └──────────────────────┘  │
│                                                                                      │
│  ┌──────────────────────┐  ┌────────────────────────────────────────────────────┐   │
│  │ Telegram Bot         │  │ Streamlit Dashboard (internal monitoring)           │   │
│  │ • Webhook listener   │  │ • Pipeline status, eval scores, session viewer      │   │
│  │ • CEO interaction    │  │                                                      │   │
│  │ • Decision buttons   │  │                                                      │   │
│  └──────────────────────┘  └────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. INTELLIGENCE ENGINE — REASONING PIPELINE

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   INTELLIGENCE ENGINE (4-Step Chain)                       │
│                                                                           │
│  INPUT: agent_role + input_data + cross_section_context + learning_context│
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │ STEP 1: DECOMPOSE                                                │     │
│  │                                                                   │     │
│  │ • Extract critical JUDGMENTS from input                           │     │
│  │ • Each judgment has: claim + evidence_needed + source             │     │
│  │ • Identifies what decisions the section MUST make                 │     │
│  │ • NOT just listing fields — listing analytical questions          │     │
│  └──────────────────────────────┬────────────────────────────────────┘     │
│                                 │                                          │
│                    ┌────────────▼────────────┐                            │
│                    │ VALIDATION: Parse       │                            │
│                    │ structured judgments     │                            │
│                    └────────────┬────────────┘                            │
│                                 │                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │ STEP 2: PRODUCE                                                  │     │
│  │                                                                   │     │
│  │ • Generate draft addressing ALL judgments                         │     │
│  │ • Must reference decomposition judgments explicitly               │     │
│  │ • Output follows schema_prompt constraints                        │     │
│  └──────────────────────────────┬────────────────────────────────────┘     │
│                                 │                                          │
│                    ┌────────────▼────────────┐                            │
│                    │ ENFORCEMENT: Check      │                            │
│                    │ judgment coverage        │◀─── If gaps found:        │
│                    │                          │     Re-produce with        │
│                    │ Did draft address ALL    │     explicit gaps listed   │
│                    │ judgments from Step 1?   │                            │
│                    └────────────┬────────────┘                            │
│                                 │                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │ STEP 3: CHALLENGE (requires reasoning_budget >= 3)               │     │
│  │                                                                   │     │
│  │ • Find typed problems: math_error, logical_gap, overconfidence,  │     │
│  │   contradiction, survivorship_bias, unsupported_claim            │     │
│  │ • Each challenge: type + location + description + fix_needed     │     │
│  └──────────────────────────────┬────────────────────────────────────┘     │
│                                 │                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │ STEP 4: REVISE                                                   │     │
│  │                                                                   │     │
│  │ • Fix each challenge with explicit checklist                     │     │
│  │ • Max 2 revision passes                                          │     │
│  └──────────────────────────────┬────────────────────────────────────┘     │
│                                 │                                          │
│                    ┌────────────▼────────────┐                            │
│                    │ ENFORCEMENT: Verify     │                            │
│                    │ challenge resolution     │                            │
│                    │                          │                            │
│                    │ If unresolved after 2:   │                            │
│                    │ → Force confidence="low" │                            │
│                    │ → Tag _unresolved list   │                            │
│                    └────────────┬────────────┘                            │
│                                 │                                          │
│  OUTPUT: (parsed_dict, metadata, token_usage)                             │
│                                                                           │
│  ANTI-GENERIC FILTERS:                                                    │
│  • Checks for CAUSAL_MARKERS (because, therefore, since...)               │
│  • Detects GENERIC_PHRASES (unique value proposition, best-in-class...)   │
│  • Flags outputs that lack causal reasoning                               │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. AGENT COMMUNICATION FLOW

```
┌───────────────────────────────────────────────────────────────────┐
│                  MESSAGE FLOW (ACL Protocol)                        │
│                                                                    │
│  Performatives: request | inform | escalate | propose | refuse     │
│  Transport: SPADE/XMPP (legacy) OR MessageBus (in-process async)  │
│                                                                    │
│                                                                    │
│       Mother Agent                                                 │
│       ┌──────────┐                                                 │
│       │          │──── request ────▶ Child Agent (do section X)    │
│       │          │◀─── inform ───── Child Agent (here's my output) │
│       │          │◀── escalate ──── Child Agent (I can't do this)  │
│       │          │                                                  │
│       │          │──── request ────▶ Devil's Advocate (review)     │
│       │          │◀─── inform ───── DA (pass/revise/reject)        │
│       │          │                                                  │
│       │          │──── request ────▶ Council Agent (for gated §s)  │
│       │          │◀─── inform ───── Council (pass/revise with fix) │
│       │          │                                                  │
│       │          │──── propose ───▶ Agent A (negotiate this value) │
│       │          │◀── propose ───── Agent A (counter-proposal)     │
│       │          │◀── refuse ────── Agent A (no deal — deadlock)   │
│       └──────────┘                                                  │
│                                                                    │
│  Every message logged to: Supabase agent_messages table            │
│  Metadata: task_id, session_id, pipeline_run_id                    │
└───────────────────────────────────────────────────────────────────┘
```

---

## 4. DATA FLOW — SECTION DEPENDENCIES

```
         Phase 1 Output (idea + assumptions + decision)
                          │
                          ▼
            ┌─────────────────────────┐
            │    §1 OPPORTUNITY       │
            │    (entry point)        │
            └──────┬─────────┬────────┘
                   │         │
          ┌────────┘         └────────┐
          ▼                           ▼
┌─────────────────┐         ┌─────────────────┐
│  §3 ENVIRONMENT │         │  §4 ORGANISATION│
│  (PEST, Porter) │         │  (structure)    │
└────────┬────────┘         └────────┬────────┘
         │                           │
         └──────────┬────────────────┘
                    ▼
          ┌─────────────────┐
          │  §5 SWOT MATRIX │
          │  (synthesis)    │
          └────────┬────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
┌─────────────────┐  ┌─────────────────┐
│ §8 MARKETING    │  │ §10 OPERATIONS  │
│ (full plan)     │  │ (production)    │
└────────┬────────┘  └────────┬────────┘
         │                    │
         └────────┬───────────┘
                  ▼
        ┌─────────────────┐
        │ §12 FINANCIAL   │◀──── SimPy (1000 Monte Carlo runs)
        │ (3-statement)   │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ §13 LAUNCH &    │
        │ CONTINGENCY     │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ EXEC SUMMARY    │◀──── reads ALL prior sections
        └─────────────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ DOCUMENT        │
        │ COMPILER        │──── → Final Markdown/PDF
        └─────────────────┘
```

---

## 5. BDI (Belief-Desire-Intention) SYSTEM

```
┌──────────────────────────────────────────────────────────────────┐
│                    AGENT BELIEF SYSTEM                             │
│                                                                   │
│  Each agent maintains persistent beliefs:                         │
│                                                                   │
│  Belief {                                                         │
│    claim: "TAM is $2.3B for SMB CRM in North America"           │
│    confidence: 0.7                                                │
│    source: "section_3" | "ceo_input" | "own_analysis" | "market" │
│    established_at: ISO timestamp                                  │
│    challenged_by: ["devils_advocate", "financial_modelling"]      │
│  }                                                                │
│                                                                   │
│  Authority Hierarchy:                                             │
│    ceo_input (3) > market_data (2) > section_3 (1) > own (0)    │
│                                                                   │
│  Rules:                                                           │
│  • Higher-authority source can override lower                     │
│  • Beliefs challenged 2+ times get confidence halved             │
│  • Beliefs inject into LLM prompts for continuity                │
│  • Persisted to Redis (survives across tasks within session)     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 6. EVALUATION HARNESS

```
┌──────────────────────────────────────────────────────────────────┐
│                    EVALUATION PIPELINE                             │
│                                                                   │
│  ┌────────────────┐                                               │
│  │ Test Ideas (5) │                                               │
│  │ • SaaS CRM     │                                               │
│  │ • Coffee sub   │                                               │
│  │ • AI consult   │                                               │
│  │ • IoT irrigate │                                               │
│  │ • Tutoring     │                                               │
│  └───────┬────────┘                                               │
│          │                                                         │
│          ▼                                                         │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ Eval Runner (bypasses SPADE — direct IE calls)          │      │
│  │                                                          │      │
│  │ For each section:                                        │      │
│  │   1. Build input_data from test idea + prior outputs    │      │
│  │   2. Call IntelligenceEngine.reason_and_produce()       │      │
│  │   3. Parse output, record latency + tokens             │      │
│  │   4. Score with scorer                                  │      │
│  └───────────────────────────┬────────────────────────────┘      │
│                              │                                     │
│          ┌───────────────────┼───────────────────┐                │
│          ▼                   ▼                   ▼                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐       │
│  │ Schema       │  │ Specificity  │  │ Completeness     │       │
│  │ Compliance   │  │ Score        │  │ Score            │       │
│  │ (fields ok?) │  │ (concrete?)  │  │ (populated?)     │       │
│  └──────────────┘  └──────────────┘  └──────────────────┘       │
│                              │                                     │
│                              ▼                                     │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ Output: JSON file in evaluation/results/                │      │
│  │ Tracked: per-section scores, latency, token usage       │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. DETAILED STATUS REPORT — WHAT'S WORKING vs NOT

### WORKING (Green)

| Component | Status | Evidence |
|-----------|--------|----------|
| L0 Input Guard | Working | Validates message format, rejects spam |
| L1 Clarity Agent | Working | Asks clarifying questions via Telegram |
| L3 Feedback Agent | Working | Presents Yes/Adjust/Kill decisions |
| Router Agent | Working | Classifies message intent |
| Memory Agent | Working | Session consolidation |
| Telegram polling | Working | CEO can interact via bot |
| Supabase reads/writes | Working | Sessions, decisions, events |
| Redis session state | Working | TTL + archival |
| Intelligence Engine (IE) | Working | 4-step chain runs, enforcement active |
| All 9 child agents | Working | All parse, all produce JSON |
| Eval runner (9 sections) | Working | Latest run: 100% parse rate |
| Scorer (schema + specificity) | Working | Produces numeric scores |
| SimPy financial simulation | Working | 1000-run Monte Carlo |
| Document Compiler | Working | JSON → Markdown conversion |
| Devil's Advocate | Working | Challenges outputs |
| Agent Beliefs (BDI) | Working | Persists to Redis |
| Pipeline Checkpoints | Working | Early-kill logic |
| Coherence Auditor | Working | Programmatic cross-checks |

### PARTIALLY WORKING (Yellow)

| Component | Issue | Impact |
|-----------|-------|--------|
| Intelligence Engine re-production | Triggers on ~50% of sections ("Draft missing N judgments") | 2x latency per section |
| Council Agent (5 personas) | Code exists, not tested in full pipeline | Quality gate bypassed |
| Negotiation Manager | Code exists, never triggered in eval runs | Conflicts go unresolved |
| Conflict Resolver | Code exists, depends on negotiation | Deadlocks not tested |
| Learning Engine | Records events, extracts patterns — but no CEO feedback data to learn from | No actual learning happening |
| Quality Gate | So-What filter + hypothesis validation exist — but eval bypasses them | Not exercised |
| SPADE/XMPP messaging | Mother Agent code uses it, eval runner bypasses it | Full pipeline untested |
| Message Bus (async) | Built as SPADE replacement, not yet wired to full flow | Unused in production |

### NOT WORKING / NOT BUILT (Red)

| Component | Status | Reason |
|-----------|--------|--------|
| Section 11 (HR Plan) | No code | `always_required: true` but no agent |
| Sections 2, 6, 7, 9 | No code | Conditional — low priority |
| Full SPADE pipeline test | Not run | Eval bypasses SPADE entirely |
| Telegram end-to-end (Phase 2) | Not tested | Last tested in Phase 1 |
| RAG Knowledge Base | Not built | Waiting for Alex's data |
| CEO feedback loop | Not active | No CEO has used Phase 2 yet |
| Scorer calibration | Broken | Sections 4,10,exec_summary score low despite good output |
| Parallel execution | Not implemented | All sections run sequentially (30 min) |
| LLM Judge (eval) | Built but unused | `evaluation/llm_judge.py` exists, not wired |

---

## 8. INTELLIGENCE REPORT — DEPTH ANALYSIS

### What "Intelligence" Means in This System

The system has **4 layers of intelligence**, each at a different maturity level:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 4: EMERGENT INTELLIGENCE (not yet achieved)                       │
│  Goal: System produces insights no single agent could alone              │
│  Status: NOT REACHED — agents don't build on each other's reasoning     │
│  Missing: Real-time cross-agent reasoning, belief propagation,           │
│           emergent contradiction discovery                                │
├─────────────────────────────────────────────────────────────────────────┤
│  LAYER 3: ADVERSARIAL QUALITY (partially built)                          │
│  Goal: System catches its own mistakes before output                     │
│  Status: PARTIAL — DA exists, Council exists, coherence auditor exists  │
│  Working: DA challenges, coherence auditor detects revenue mismatches    │
│  Missing: Council not tested end-to-end, DA findings not fed back into  │
│           revision loop in eval path                                      │
├─────────────────────────────────────────────────────────────────────────┤
│  LAYER 2: ENFORCED REASONING (working but brittle)                       │
│  Goal: LLM output is validated between steps                             │
│  Status: WORKING — IE judgment coverage + challenge resolution checks   │
│  Working: Re-production on gaps, max 2 revision passes, confidence      │
│           downgrade on unresolved challenges                              │
│  Issue: Re-production triggers too often (strict judgment matching)      │
│           → doubles latency without always improving quality              │
├─────────────────────────────────────────────────────────────────────────┤
│  LAYER 1: STRUCTURED OUTPUT (solid)                                      │
│  Goal: Agents produce valid JSON matching Pydantic schemas               │
│  Status: FULLY WORKING — 100% parse rate on latest eval run             │
│  Working: Schema validation, fallback defaults, markdown stripping       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Intelligence Engine — Quantified Performance

| Metric | Value | Assessment |
|--------|-------|------------|
| Parse success rate | 100% (9/9 sections) | Excellent |
| Average score (all sections) | 7.7/10 | Good |
| Top sections (§1, §3, §5, §8, §13) | 9.5-10/10 | Excellent reasoning |
| Bottom sections (§4, §10, exec) | 2.6-6.7/10 | Scorer miscalibration OR weak output |
| Re-production trigger rate | ~50% | Too aggressive — wastes tokens |
| Average latency per section | 200s (~3.3 min) | Acceptable but sequential = 30 min total |
| Token usage per section | ~13K output tokens | Within budget |
| Confidence honesty | All "low" | Correct (sparse input) but scorer penalizes |

### What Makes This System "Intelligent" vs Just "Structured"

**Genuinely intelligent features (beyond template-filling):**

1. **Judgment enforcement** — IE doesn't just ask the LLM to produce output. It first identifies what analytical decisions are needed, then verifies the output addressed them.

2. **Anti-generic detection** — The IE has a list of 15 generic business phrases and checks if the output relies on them instead of specific reasoning.

3. **Causal marker validation** — Checks for words like "because", "therefore", "since" — penalizes outputs that make claims without causal chains.

4. **Cross-section context injection** — Each agent receives relevant data from prior sections and must reason about it (not just dump it).

5. **Adversarial challenge** — Devil's Advocate exists to tear apart outputs. It's prompted to be "brutal but fair" and must cite specific claims.

6. **Belief persistence (BDI)** — Agents don't start fresh each call. They carry beliefs from earlier sections and must reconcile new evidence.

7. **Negotiation protocol** — When agents contradict each other, they can negotiate (3 rounds) before escalating.

8. **Pipeline early-kill** — If Section 1 signals a doomed idea (generic strategy + low confidence + high uncertainty), the pipeline can terminate early.

**What's NOT intelligent yet (just structured prompting):**

1. **Agent prompts are still template-focused** — Most child agents have "Return ONLY valid JSON with these fields" prompts, not reasoning frameworks. (The critique doc proposes fixes.)

2. **Learning is passive** — The engine records what happened but doesn't change behavior across runs.

3. **No real negotiation has been tested** — The code exists but the eval path never triggers a contradiction between sections.

4. **Cross-section awareness is one-directional** — Agents receive context but don't self-audit against it before returning.

5. **Council never fires in eval** — The 5-persona deliberation is code-complete but never exercises in the test path.

---

## 9. WHAT ELSE CAN BE VISUALLY REPRESENTED

| Visualization | Description | Value |
|---------------|-------------|-------|
| **Score heatmap over time** | Grid of eval runs × sections, colored by score | Shows improvement trajectory |
| **Token flow Sankey diagram** | How tokens flow through IE steps (decompose → produce → challenge → revise) | Identifies where tokens are wasted |
| **Latency waterfall** | Gantt-style chart showing sequential section execution | Makes parallelization gains obvious |
| **Belief propagation graph** | Network diagram of how beliefs flow between agents | Shows where information gets stuck |
| **Confidence chain** | Trace how confidence labels propagate (if §3 is "low", does §5 respect that?) | Validates the confidence chain rule |
| **Learning engine pattern timeline** | When failures happened, what patterns were extracted, did they prevent repeats | Shows whether learning actually works |
| **Message sequence diagram** | UML-style sequence of all ACL messages in a full pipeline run | Reveals communication bottlenecks |
| **Kill checkpoint decision tree** | Flowchart of when/why pipeline terminates early | Validates early-kill logic |

---

## 10. SUMMARY — PROJECT HEALTH SCORECARD

```
┌────────────────────────────────────────────┬───────┬─────────────────────────┐
│ Dimension                                   │ Score │ Notes                    │
├────────────────────────────────────────────┼───────┼─────────────────────────┤
│ Schema compliance (does it produce JSON?)   │ 10/10 │ 100% parse rate         │
│ Reasoning depth (IE enforcement)            │  7/10 │ Working but over-triggers│
│ Quality gates (DA + Council)                │  4/10 │ DA works, Council untested│
│ Cross-section coherence                     │  5/10 │ Auditor exists, not active│
│ Learning & adaptation                       │  3/10 │ Records events, no adapt │
│ Communication (multi-agent)                 │  4/10 │ Hub-spoke only, no P2P   │
│ End-to-end integration                      │  3/10 │ Eval works, SPADE doesn't│
│ Performance                                 │  3/10 │ 30 min sequential        │
│ Eval infrastructure                         │  8/10 │ Solid, needs calibration │
│ CEO experience (Telegram)                   │  6/10 │ Phase 1 works, Phase 2 no│
├────────────────────────────────────────────┼───────┼─────────────────────────┤
│ OVERALL                                     │ 5.3/10│ Structurally complete,   │
│                                             │       │ intelligence is shallow  │
└────────────────────────────────────────────┴───────┴─────────────────────────┘
```

**Bottom line:** The system produces a complete 9-section business plan that parses correctly. But it's closer to "structured LLM output assembly" than "multi-agent intelligence." The intelligence infrastructure (IE, DA, Council, Negotiation, Learning, BDI) is *built* but most of it doesn't fire in the actual execution path. The next leap is wiring these components together so they actually exercise during a real pipeline run.
