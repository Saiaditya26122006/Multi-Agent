# 🚀 Implementation Plan — Multi-Agent System Hardening

**Status:** PLANNING PHASE  
**Last Updated:** 2026-07-19  
**Total Issues:** 6  
**Total Implementation Tasks:** TBD

---

## Quick Reference

| # | Risk | Severity | Week | Status | Effort |
|---|------|----------|------|--------|--------|
| 1 | Hallucination via Weak RAG | HIGH | 1 | ⏳ Planning | 3-5 days |
| 2 | Context Explosion | MEDIUM-HIGH | 2 | ⏳ Planning | 2-3 days |
| 3 | Silent Failures | MEDIUM | 1 | ⏳ Planning | 1-2 days |
| 4 | No Monitoring/Observability | MEDIUM | 3 | ⏳ Planning | 2-3 weeks |
| 5 | Dependency Deadlock | MEDIUM | 1 | ⏳ Planning | 1-2 hours |
| 6 | No Feedback Loop | MEDIUM | 3 | ⏳ Planning | 1-2 weeks |

---

## 🔴 RISK #1: Hallucination via Weak RAG

**Severity:** HIGH  
**Week:** 1 (BLOCKER)  
**Impact:** Agents claim unsupported facts → CEO ships false claims → investor trust destroyed  

### Problem Summary
- Single RAG retrieval pass (generic query)
- No claim-level verification after LLM output
- Confidence not tied to grounding score
- 0% grounding can still produce "HIGH confidence" output

### Current Code Issues
- `base_child_agent.py:371` — One-time RAG retrieval
- `_verify_rag_grounding()` — Only checks if RAG was used, not if claims match
- `_required_output_precheck()` — Keyword overlap only (insufficient)

### Implementation Plan

**To be filled in during detailed design phase**

```
STEP 1: Design claim extraction
  - [ ] Define what constitutes a "factual claim" in each section output
  - [ ] Create extraction rules per agent type
  - [ ] Handle numeric claims, boolean claims, list claims

STEP 2: Implement multi-pass RAG verification
  - [ ] Create `_verify_claims_against_rag()` method
  - [ ] Loop through extracted claims
  - [ ] Query RAG for each claim individually (top_k=5, threshold=0.4)
  - [ ] Track which claims verified vs. unverified

STEP 3: Enforce confidence ceiling
  - [ ] Add logic: confidence_score = min(confidence_score, grounding_score)
  - [ ] Hard threshold: if grounding < 0.4, trigger revision (don't accept)

STEP 4: Add revision strategy
  - [ ] If grounding < 0.4, agent must regenerate with explicit grounding requirement
  - [ ] Max 2 revision attempts before escalation

STEP 5: Testing
  - [ ] Unit tests for claim extraction
  - [ ] Integration tests for RAG verification
  - [ ] Test with known false claims (should reject)
  - [ ] Test with known true claims (should accept)
```

### Success Criteria
- ✅ All agent outputs have grounding_score >= 0.7
- ✅ Zero "HIGH confidence" outputs with grounding < 0.5
- ✅ Confidence score visibly demoted when grounding is weak

### Dependencies
- None (self-contained)

### Files to Modify
- `agents/phase2/base_child_agent.py`
- `agents/phase2/rag_mixin.py` (if needed for retrieval)

---

## 🟠 RISK #2: Context Explosion

**Severity:** MEDIUM-HIGH  
**Week:** 2  
**Impact:** Late sections get squeezed → incomplete reasoning → garbled outputs  

### Problem Summary
- Group 1 outputs 4KB
- Group 2 receives Group 1 + generates 3KB
- Group 3 receives Groups 1-2 + generates 5KB
- Group 4 receives Groups 1-3 (~15KB) but only has 4096 token limit

### Current Code Issues
- `mother_agent.py` — No context budget tracking
- `cross_section_context` dict grows unbounded
- No compression strategy
- No fallback if context overflows

### Implementation Plan

**To be filled in during detailed design phase**

```
STEP 1: Add context budgeting
  - [ ] Create `_estimate_tokens()` helper for dict/text
  - [ ] Add budget tracking to `_run_group()` method
  - [ ] Max budget: 3000 tokens for cross_section_context

STEP 2: Implement compression
  - [ ] Create `_compress_context()` method
  - [ ] Summarize Group 1-2 outputs into 500-word summaries
  - [ ] Keep only top 3 findings per group

STEP 3: Add pre-flight check
  - [ ] Before sending to agent: verify tokens(prompt + context + budget) <= 4096
  - [ ] If overflow: apply compression
  - [ ] If still overflow: escalate (don't truncate silently)

STEP 4: Testing
  - [ ] Generate large outputs and verify they compress
  - [ ] Verify Group 4 doesn't lose critical info
```

### Success Criteria
- ✅ All groups complete without token overflow
- ✅ No silent truncation warnings
- ✅ Group 4 outputs are complete and coherent

### Dependencies
- Requires Risk #1 (hallucination fix) to be partially done for accurate output size estimates

### Files to Modify
- `agents/phase2/mother_agent.py`

---

## 🟠 RISK #3: Silent Failures

**Severity:** MEDIUM  
**Week:** 1 (QUICK WIN)  
**Impact:** Agent fails → CEO gets corrupted output → discovered too late  

### Problem Summary
- Agent escalates to Mother Agent but no alert sent
- No retry logic for failed agents
- Output marked `_status="refused"` but pipeline continues
- CEO sees incomplete plan (missing financial projections, etc.)

### Current Code Issues
- `base_child_agent.py:505-515` — Escalation with no follow-up
- `mother_agent.py:147` — Logs escalation but doesn't alert
- No retry counter
- No minimum availability threshold

### Implementation Plan

**To be filled in during detailed design phase**

```
STEP 1: Add escalation alerting
  - [ ] In `mother_agent.py handle_escalate()`: send Web Interface alert
  - [ ] Message format: "⚠️ {agent_name} failed: {reason}"
  - [ ] Include session_id for debugging

STEP 2: Implement retry logic
  - [ ] Add `retry_count` field to escalation message
  - [ ] Max 2 retry attempts per agent
  - [ ] Wait 5s between retries (backoff)
  - [ ] Log each retry attempt

STEP 3: Add group-level availability check
  - [ ] If >1 agent fails in same group, pause pipeline
  - [ ] Alert CEO: "Multiple agents failed in {group}, pausing"
  - [ ] Wait for manual review before continuing

STEP 4: Add degradation flag
  - [ ] Mark pipeline run with `partial_output=true` if any agent escalated
  - [ ] Flag this in executive summary: "⚠️ Some sections incomplete"
  - [ ] Require CEO approval before delivery

STEP 5: Testing
  - [ ] Simulate agent timeout → verify alert sent
  - [ ] Simulate 2 failures in same group → verify pause
  - [ ] Verify retry attempts are logged
```

### Success Criteria
- ✅ CEO alerted within 30s of agent escalation
- ✅ Agent automatically retried once before alerting
- ✅ Pipeline pauses if >1 agent fails in group
- ✅ Partial outputs clearly flagged

### Dependencies
- Requires Web Interface integration (already exists)

### Files to Modify
- `agents/phase2/mother_agent.py`
- `agents/phase2/base_child_agent.py` (escalation format)

---

## 🟠 RISK #4: No Monitoring/Observability

**Severity:** MEDIUM  
**Week:** 3 (OPERATIONS)  
**Impact:** Latency climbs 3x, cost climbs 3x, nobody notices until too late  

### Problem Summary
- Logs stored in Supabase but no dashboards
- No SLO tracking (latency, availability)
- No grounding score tracking
- No cost monitoring (Bedrock usage)
- No alerting rules

### Current Code Issues
- `events_logs` table exists but unused
- No dashboards
- No alerting infrastructure

### Implementation Plan

**To be filled in during detailed design phase**

```
STEP 1: Design monitoring schema
  - [ ] Define metrics to track:
    - Pipeline latency (per group, per agent)
    - Grounding score distribution
    - Escalation rate (% per group)
    - Bedrock token usage (per model, per day)
    - CEO approval rate

STEP 2: Create dashboard (Streamlit or similar)
  - [ ] Real-time metrics display
  - [ ] Historical trends (7-day, 30-day)
  - [ ] Failure log view
  - [ ] Cost breakdown

STEP 3: Implement alerting rules
  - [ ] Alert if latency > 120s (2min baseline)
  - [ ] Alert if escalation rate > 10% in group
  - [ ] Alert if grounding score < 0.6 (average)
  - [ ] Alert if daily cost > $50 (or threshold)
  - [ ] Send alerts to Web Interface

STEP 4: Add metrics collection
  - [ ] Log start/end times for each group
  - [ ] Log grounding scores to events_logs
  - [ ] Track token usage from Bedrock responses
  - [ ] Track escalation events

STEP 5: Testing
  - [ ] Verify metrics appear in dashboard
  - [ ] Simulate high latency → verify alert sent
  - [ ] Simulate high escalation rate → verify alert sent
```

### Success Criteria
- ✅ Dashboard updated real-time
- ✅ Alerts sent < 1/week (false positive rate acceptable)
- ✅ Historical metrics queryable (7-30 days)
- ✅ CEO can view own metrics

### Dependencies
- Requires events_logs populated correctly (depends on Risk #3)

### Files to Create
- `dashboards/monitoring.py` (Streamlit app)
- `services/metrics_service.py` (metric collection)
- `alerts/alerting.py` (alert rules)

---

## 🟠 RISK #5: Dependency Deadlock

**Severity:** MEDIUM  
**Week:** 1 (1-HOUR FIX)  
**Impact:** One typo in dependency_map.yaml → Mother Agent deadlocks  

### Problem Summary
- No cycle detection in dependency_map.yaml
- If Group 1 accidentally depends on Group 4, Mother Agent waits forever
- No validation on startup

### Current Code Issues
- `dependency_map.yaml` loaded without validation
- No graph cycle detection

### Implementation Plan

**To be filled in during detailed design phase**

```
STEP 1: Create cycle detection function
  - [ ] Implement DFS-based cycle detector
  - [ ] Return cycle path if found (for debugging)

STEP 2: Add startup validation
  - [ ] In Mother Agent `__init__()`: call cycle detection
  - [ ] Raise error if cycle found
  - [ ] Log cycle path for debugging

STEP 3: Testing
  - [ ] Create test with valid DAG → passes
  - [ ] Create test with cycle → fails with error
  - [ ] Verify error message is clear
```

### Success Criteria
- ✅ Mother Agent rejects cyclic dependency_map on startup
- ✅ Clear error message showing cycle path
- ✅ < 5s validation time

### Dependencies
- None

### Files to Modify
- `agents/phase2/mother_agent.py` (startup validation)

---

## 🟠 RISK #6: No Feedback Loop

**Severity:** MEDIUM  
**Week:** 3 (OPTIONAL)  
**Impact:** CEO corrections not auto-learned → same mistakes repeat  

### Problem Summary
- CEO says "wrong market size"
- Knowledge Graph stores correction
- Next pipeline run, agent guesses same wrong number again
- Agents don't query for corrections before reasoning

### Current Code Issues
- `learning_engine.py` exists but not hooked
- Corrections stored in RAG as `source_type="correction"` but not prioritized
- No "learned vs. new" distinction in retrieval

### Implementation Plan

**To be filled in during detailed design phase**

```
STEP 1: Prioritize corrections in RAG
  - [ ] Add weight boost to `source_type="correction"` retrieval
  - [ ] 2x weight for corrections vs. normal facts

STEP 2: Query for corrections pre-reasoning
  - [ ] In `handle_request()`: explicitly query for "CEO corrections" in section
  - [ ] Include corrections in learning_context before LLM call

STEP 3: Add preference tracking
  - [ ] Track which corrections were actually followed
  - [ ] If agent ignores correction 2x, escalate to CEO

STEP 4: Testing
  - [ ] CEO corrects fact → verify agent uses corrected fact next run
  - [ ] Verify correction weight is higher than original
```

### Success Criteria
- ✅ Agents automatically use CEO corrections in next run
- ✅ Corrections are visibly sourced in agent output
- ✅ No repeated mistakes after correction

### Dependencies
- Requires Risk #1 (hallucination fix) to trust grounding

### Files to Modify
- `agents/phase2/rag_mixin.py`
- `agents/phase2/base_child_agent.py` (learning_context injection)

---

## 📊 Summary Table

| Risk | Blocker? | Effort | Start | Duration | Blocks | Blocked By |
|------|----------|--------|-------|----------|--------|-----------|
| Hallucination | ✅ YES | 3-5d | Week 1 | 3-5 days | #2, #4, #6 | None |
| Context | No | 2-3d | Week 2 | 2-3 days | None | None |
| Failures | No | 1-2d | Week 1 | 1-2 days | #4 | None |
| Monitoring | No | 2-3w | Week 3 | 2-3 weeks | None | #1, #3 |
| Deadlock | No | 1h | Week 1 | 1-2 hours | None | None |
| Feedback | No | 1-2w | Week 3 | 1-2 weeks | None | #1 |

---

## 🏗️ ARCHITECTURE QUALITY IMPROVEMENTS (Not Risks, But Critical)

These are architectural enhancements that aren't failures, but MUST be in place before scaling.

### A-1: Input Validation Architecture

**Status:** Partial (feed_handler exists but not hooked to all agents)  
**Priority:** HIGH  
**Effort:** 2-3 days  

**Problem:**
- Not all agents validate input schemas
- Garbage in → garbage out (LLM receives malformed tasks)
- No clear single validation point

**Solution:**
```
STEP 1: Create InputValidator class
  - [ ] Central validation for all agent inputs
  - [ ] Type checking, range checking, format checking
  - [ ] Returns ValidationError with detailed messages

STEP 2: Hook into all agent handle_request() methods
  - [ ] Before any processing: validate_input(input_data)
  - [ ] Escalate if validation fails

STEP 3: Test with invalid inputs
  - [ ] Test each agent with garbage input
  - [ ] Verify clear error messages
```

**Files:** `agents/phase2/input_validator.py` (new), all agent files

---

### A-2: State Machine Architecture

**Status:** Defined but not enforced  
**Priority:** HIGH  
**Effort:** 3-4 days  

**Problem:**
- Valid states: NEEDS_CLARIFICATION, AWAITING_RESEARCH, etc.
- But no validation that transitions are legal
- Agent can jump from AWAITING_APPROVAL → COMPLETED without APPROVAL

**Solution:**
```
STEP 1: Define state transition graph
  - [ ] Map all valid transitions
  - [ ] Create transition matrix (state X action → next_state)
  - [ ] Invalid transitions raise StateTransitionError

STEP 2: Enforce transitions in session management
  - [ ] Before setting new state: check if transition valid
  - [ ] Log all state changes with timestamp
  - [ ] Audit trail of who changed what when

STEP 3: Test state machine
  - [ ] Unit tests for each valid transition
  - [ ] Unit tests for invalid transitions (should fail)
```

**Files:** `services/state_machine.py` (new), `services/session_store.py` (modify)

---

### A-3: Data Validation at DB Boundaries

**Status:** Partial (Pydantic schemas exist but not enforced at DB writes)  
**Priority:** HIGH  
**Effort:** 2-3 days  

**Problem:**
- Agent produces output → writes to Supabase
- But data isn't re-validated before write
- Bad data corrupts canon

**Solution:**
```
STEP 1: Create DatabaseValidator
  - [ ] Before any .insert()/.update(): validate against schema
  - [ ] Check foreign keys exist
  - [ ] Check enum values are in allowed set
  - [ ] Check NOT NULL fields aren't null

STEP 2: Add validation to SupabaseClient wrapper
  - [ ] All writes go through validation
  - [ ] Return ValidationError if invalid

STEP 3: Test
  - [ ] Try to insert bad enum → fails
  - [ ] Try to insert null to NOT NULL field → fails
```

**Files:** `database/validators.py` (new), `database/supabase_client.py` (modify)

---

### A-4: Testing Architecture

**Status:** Exists but incomplete  
**Priority:** HIGH (BLOCKER for production)  
**Effort:** 1-2 weeks  

**Problem:**
- Tests hit live Supabase (slow, flaky)
- No mock database for fast testing
- No test fixtures for common scenarios
- No e2e test workflow

**Solution:**
```
STEP 1: Create test fixture factory
  - [ ] Fixtures for: valid agent input, valid CEO data, valid RAG context
  - [ ] Fixtures for: invalid input, corrupted data, missing dependencies
  - [ ] Use factories for DRY

STEP 2: Add unit test mocks
  - [ ] Mock Supabase client
  - [ ] Mock Bedrock LLM responses
  - [ ] Mock RAG retrieval
  - [ ] Tests run in 100ms instead of 2s

STEP 3: Add integration test suite
  - [ ] Hit real Supabase, but with test session isolation
  - [ ] Full pipeline: input → agent → output → DB
  - [ ] Cleanup after each test

STEP 4: Add e2e test workflow
  - [ ] Send raw CEO data through feed handler
  - [ ] Run full 4-group pipeline
  - [ ] Verify output in DB and correct section

STEP 5: Add CI/CD gates
  - [ ] Unit tests required to pass before merge
  - [ ] Integration tests run nightly (can be slow)
```

**Files:** `tests/fixtures/` (new), `tests/conftest.py` (modify), `.github/workflows/` (new)

---

### A-5: Security Architecture

**Status:** Minimal (API keys in .env, but no access control)  
**Priority:** MEDIUM (defer until after Phase 1)  
**Effort:** 1-2 weeks  

**Problem:**
- No row-level access control (CEO can theoretically read other CEO data)
- No audit trail for data access
- No encryption at rest (Supabase handles this, but app-level encryption missing)
- No rate limiting (agent could spam Bedrock)

**Solution:**
```
STEP 1: Add session-level access control
  - [ ] session_id linked to ceo_id
  - [ ] Agent can only read/write within own session
  - [ ] Query filters enforce session_id on all selects

STEP 2: Add audit logging
  - [ ] Log all data access (read/write/delete)
  - [ ] Include: who, what, when, why
  - [ ] Store in audit_logs table

STEP 3: Add rate limiting
  - [ ] Limit Bedrock calls per session: max 100/hour
  - [ ] Limit RAG queries per agent: max 50/run
  - [ ] Return 429 Too Many Requests if exceeded

STEP 4: Add encryption for sensitive fields
  - [ ] Encrypt CEO personal data at app level
  - [ ] Decrypt only when needed
  - [ ] Never log decrypted values
```

**Files:** `auth/access_control.py` (new), `services/audit_logger.py` (new), `services/rate_limiter.py` (new)

---

### A-6: Caching Strategy

**Status:** None (every agent re-fetches from RAG)  
**Priority:** MEDIUM  
**Effort:** 3-4 days  

**Problem:**
- Agent 1 queries RAG for "CEO market data" → 400ms
- Agent 2 queries same data → 400ms (cache miss)
- Wasted 800ms + Bedrock embedding cost

**Solution:**
```
STEP 1: Add query cache (Redis)
  - [ ] Cache RAG queries by hash(query, section)
  - [ ] TTL: 24 hours (refresh daily)
  - [ ] Hit rate tracking

STEP 2: Add LLM response cache
  - [ ] Cache LLM outputs by hash(input)
  - [ ] Only for deterministic tasks (e.g., structuring)
  - [ ] NOT for reasoning (outputs should vary)

STEP 3: Add agent output cache
  - [ ] Cache agent outputs by session
  - [ ] Reuse if agent called twice in same session
  - [ ] Invalidate on CEO correction

STEP 4: Measure improvement
  - [ ] Baseline: average pipeline latency without cache
  - [ ] With cache: target < 30s (vs. 45s baseline)
```

**Files:** `services/cache_service.py` (new), `agents/phase2/base_child_agent.py` (modify)

---

### A-7: Logging & Observability Beyond Metrics

**Status:** Basic (events_logs exists but minimal)  
**Priority:** MEDIUM  
**Effort:** 1-2 weeks  

**Problem:**
- No structured logging (just text blobs in events_logs)
- No log levels (everything logged at same level)
- Hard to query/debug specific issues
- No correlation ID tracing across agents

**Solution:**
```
STEP 1: Migrate to structured logging
  - [ ] Replace print()/logging.info() with structured logs
  - [ ] Format: {timestamp, level, agent, action, input, output, status}
  - [ ] Use JSON for structured parsing

STEP 2: Add correlation IDs
  - [ ] Generate correlation_id at session start
  - [ ] Pass through all agent messages
  - [ ] Trace entire request flow with one ID

STEP 3: Add log levels
  - [ ] DEBUG: detailed info for devs
  - [ ] INFO: normal operations
  - [ ] WARN: unexpected but recoverable
  - [ ] ERROR: agent failure, escalation
  - [ ] CRITICAL: system failure, halt

STEP 4: Add log rotation
  - [ ] Supabase events_logs grows fast
  - [ ] Archive old logs to S3
  - [ ] Keep hot 30 days, archive >30 days

STEP 5: Add query interface
  - [ ] Dashboard to search logs by correlation_id
  - [ ] Filter by agent, status, time range
  - [ ] Export logs for debugging
```

**Files:** `services/structured_logger.py` (new), `dashboards/log_explorer.py` (new)

---

### A-8: Configuration Management

**Status:** Partial (.env exists, but config not versioned)  
**Priority:** MEDIUM  
**Effort:** 2-3 days  

**Problem:**
- Config scattered: .env, dependency_map.yaml, agent_roster.yaml, CLAUDE.md
- No way to roll back config
- No way to A/B test config changes
- LLM model IDs hardcoded (should be configurable)

**Solution:**
```
STEP 1: Create ConfigManager class
  - [ ] Central source for all config
  - [ ] Load from: .env, config.yaml, environment
  - [ ] Validate config on startup

STEP 2: Add config versioning
  - [ ] Store config.yaml in Git
  - [ ] Tag each production config release
  - [ ] Easy rollback: git checkout config.yaml@v1.2.3

STEP 3: Add feature flags
  - [ ] Enable/disable features per session
  - [ ] Test new features with subset of CEO sessions
  - [ ] Example: "enable_feedback_loop=false" → skip Risk #6

STEP 4: Add config hot-reload
  - [ ] Change config → applies without restart
  - [ ] Fallback: restart if hot-reload fails

STEP 5: Document all config options
  - [ ] README with all config keys
  - [ ] Default values
  - [ ] Valid ranges
```

**Files:** `config/manager.py` (new), `config/config.yaml` (new), `config/schema.json` (new)

---

### A-9: Async/Concurrency Model

**Status:** Partial (SPADE agents are async, but coordination unclear)  
**Priority:** MEDIUM  
**Effort:** 1-2 weeks  

**Problem:**
- 4 execution groups → unclear how parallel agents coordinate
- No deadlock detection
- No timeout per agent (waits forever)
- No graceful shutdown

**Solution:**
```
STEP 1: Define concurrency model
  - [ ] Group 1 agents: 3 parallel (Opportunity, Environment, Marketing)
  - [ ] Timeout per agent: 60s (configurable)
  - [ ] Timeout per group: 120s (max latency)

STEP 2: Add group-level coordination
  - [ ] Wait for all agents in group before proceeding
  - [ ] If any agent times out: escalate + continue
  - [ ] Track which agents completed

STEP 3: Add deadlock detection
  - [ ] If group hangs > 2min: kill it, escalate
  - [ ] Log which agent caused hang
  - [ ] Retry with reduced complexity

STEP 4: Add graceful shutdown
  - [ ] On SIGTERM: cancel in-flight requests
  - [ ] Wait max 30s for agents to cleanup
  - [ ] Force kill after 30s
```

**Files:** `agents/phase2/group_executor.py` (new), `agents/phase2/mother_agent.py` (modify)

---

### A-10: Disaster Recovery & Rollback

**Status:** None  
**Priority:** LOW (but critical for scale)  
**Effort:** 1-2 weeks  

**Problem:**
- Agent produces bad output → written to DB
- No way to rollback to previous version
- No backup of historical outputs

**Solution:**
```
STEP 1: Add output versioning
  - [ ] Each agent output gets version number
  - [ ] Store old versions in history table
  - [ ] Easy to view diff between versions

STEP 2: Add backup strategy
  - [ ] Daily backup of knowledge_base table to S3
  - [ ] Weekly backup of full DB
  - [ ] Test restore monthly

STEP 3: Add rollback procedure
  - [ ] CEO can rollback session to previous output
  - [ ] Rollback resets state machine to AWAITING_FEEDBACK
  - [ ] Agent re-runs with different parameters

STEP 4: Add incident playbook
  - [ ] If agent produces 100% hallucinated output:
    1. Pause pipeline
    2. CEO reviews & rejects
    3. Agent auto-revises
    4. If still bad: escalate to manual review
```

**Files:** `database/migrations/add_output_versioning.sql` (new), `services/backup_service.py` (new)

---

## 📝 Phase 1 (Week 1) — CRITICAL BLOCKERS

**Do these first. Everything else waits.**

- [ ] **Risk #1:** Hallucination fix (claim verification)
- [ ] **Risk #3:** Silent failures (escalation alerting + retry)
- [ ] **Risk #5:** Dependency deadlock (cycle detection)

**Estimated duration:** 5-7 days

---

## 📝 Phase 2 (Week 2) — QUALITY

**After Phase 1 is complete and tested.**

- [ ] **Risk #2:** Context explosion (budget tracking + compression)

**Estimated duration:** 2-3 days

---

## 📝 Phase 3 (Week 3) — OPERATIONS & LEARNING

**After Phase 1-2 stable.**

- [ ] **Risk #4:** Monitoring/observability (dashboards + alerting)
- [ ] **Risk #6:** Feedback loop (correction prioritization)

**Estimated duration:** 3-5 weeks

---

## 🎯 Production Readiness Gate

**Before running pipeline with CEO daily:**

✅ All Phase 1 + 2 complete and tested  
✅ Grounding score >= 0.7 on all outputs  
✅ Escalation rate < 5% per group  
✅ Pipeline latency stable (< 60s)  
✅ Monitoring dashboard live  
✅ CEO approval rate > 80%  
✅ No recurring failure pattern  

---

## ✏️ Notes for Discussion

**TBD during implementation phase:**

- Exact grounding threshold (0.4? 0.5?)
- Revision attempt limit (2? 3?)
- Context compression strategy (summary only? top N findings?)
- Alerting thresholds (latency > 120s? escalation > 10%?)
- Feedback loop weight boost (2x? 3x?)

---

**Last Updated:** 2026-07-19 by Claude Code  
**Next Step:** Review this plan, then say "start implementing" when ready for Phase 1
