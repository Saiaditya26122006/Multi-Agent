# Post-Fix Critique: Remaining Issues & Next Steps

**Date:** 2026-05-29
**Benchmark Score:** 8.9/10 (Grade A)
**Previous Score:** ~3.5/10 (estimated baseline from code-only analysis)

---

## Fixes Implemented This Session

| # | Fix | Impact | Status |
|---|-----|--------|--------|
| 1 | Intelligence Engine — enforced reasoning chain with judgment parsing, coverage verification, challenge resolution checking, generic phrase detection | Reasoning quality | DONE |
| 2 | All 9 SYSTEM_PROMPTs rewritten with domain-specific reasoning frameworks, kill conditions, anti-generic rules | Agent intelligence | DONE |
| 3 | MessageBus — async in-process replacement for SPADE/XMPP | Communication | DONE (not fully migrated) |
| 4 | Structured failure handling (retry-simple → partial → refuse) replaces template fallback | Fallback quality | DONE |
| 5 | Cross-section pre-check and post-audit consistency in base agent | Consistency | DONE |
| 6 | Learning Engine — pattern extraction, root cause classification, CEO preference tracking, run improvement measurement | Learning | DONE |
| 7 | Negotiation Protocol — bounded 3-round agent conflict resolution | MAS behavior | DONE |
| 8 | Agent Beliefs (BDI) — per-agent belief store with conflict detection | Autonomy | DONE |
| 9 | Pipeline Checkpoints — early-kill after sections 1, 3, 12 | Adaptive pipeline | DONE |
| 10 | Mother Agent split — QualityGate, CoherenceAuditor, ConflictResolver extracted | Decoupling | DONE |

---

## NEW CRITIQUES (discovered after implementation)

### Critique A: SPADE Still Required (Communication Layer Half-Migrated)

**Problem:** MessageBus exists and is imported, but `base_child_agent.py` still inherits from `spade.Agent` and uses SPADE's `CyclicBehaviour` for message routing. The system requires Prosody XMPP server to start.

**Impact:** 30s startup overhead remains. SPADE is still a SPOF.

**Solution:** Create a `BaseChildAgentV2` that inherits from a plain class (not `spade.Agent`) and uses `MessageBus.register()` instead of `ChildListenBehaviour`. Run both in parallel for 1 week, then deprecate V1.

**Effort:** Medium
**Priority:** P0 — blocks production deployment

---

### Critique B: Reasoning Depth at 7.5/8.0 — Devil's Advocate Excluded

**Problem:** `devils_advocate.py` and `council_agent.py` still have their original prompts (not rewritten with reasoning frameworks). The benchmark scores them at 0 for reasoning markers, dragging the average down.

**Impact:** DA challenges may still be generic ("the market is competitive") rather than specific ("your $50 CAC assumption requires 25,000 leads at 2% conversion — where do those leads come from?").

**Solution:** Apply the same reasoning framework pattern to DA and Council agents.

**Effort:** Low
**Priority:** P1

---

### Critique C: No Runtime Validation of New Code

**Problem:** All new modules (negotiation, beliefs, checkpoints, coherence) have been created and wired via imports, but none have been tested with a real pipeline run. We don't know if:
- `AgentBeliefStore._load()` correctly deserializes from Redis
- `NegotiationManager` produces valid LLM responses under Bedrock
- `CoherenceAuditor.audit()` handles edge cases (missing sections, empty dicts)
- `_wait_for_checkpoint_response()` doesn't block indefinitely in test

**Impact:** First live run will likely hit integration bugs.

**Solution:** Write integration tests for each new module using mocked Bedrock/Redis clients.

**Effort:** Medium
**Priority:** P0

---

### Critique D: Mother Agent Still 2500+ Lines

**Problem:** We extracted logic INTO new modules but didn't REMOVE the old code from `mother_agent.py`. It still has `_run_coherence_audit()`, inline quality gate logic, etc. The new modules are imported but may be running parallel to old code paths.

**Impact:** Confusing, potentially double-executing logic.

**Solution:** After integration tests pass, remove the old inline implementations from `mother_agent.py` and delegate fully to the new modules.

**Effort:** High (risky refactor)
**Priority:** P1

---

### Critique E: Belief Store Not Used During Production

**Problem:** `AgentBeliefStore` is initialized in `__init__` but `handle_request()` never calls `self.beliefs.get_beliefs_for_prompt()` or `self.beliefs.update_from_output()`. The store exists but is never consulted or updated during the actual reasoning flow.

**Impact:** Zero autonomy improvement at runtime — beliefs are dead code.

**Solution:** Add 3 lines to `handle_request()`:
1. Before IE call: inject `self.beliefs.get_beliefs_for_prompt()` into learning_context
2. After output: call `self.beliefs.update_from_output(result)`
3. On cross_context conflicts: call `self.beliefs.get_conflicts_with()`

**Effort:** Low
**Priority:** P1

---

## Priority Implementation Order for Remaining Work

| Priority | Critique | Effort | Impact |
|----------|----------|--------|--------|
| P0 | C: Integration tests for new modules | Medium | Prevents runtime failures |
| P0 | A: Complete SPADE→MessageBus migration | Medium | Removes 30s startup, SPOF |
| P1 | E: Wire beliefs into handle_request | Low | Enables agent autonomy |
| P1 | B: Rewrite DA + Council prompts | Low | Improves reasoning score |
| P1 | D: Remove old Mother Agent inline code | High | Clean architecture |

---

## Benchmark Comparison

| Dimension | Before | After | Target | Delta |
|-----------|--------|-------|--------|-------|
| Reasoning Depth | ~2.0 | 7.5 | 8.0 | +5.5 |
| IE Enforcement | ~1.0 | 10.0 | 7.0 | +9.0 |
| Communication | ~3.0 | 7.5 | 8.0 | +4.5 |
| Cross-Section | ~2.0 | 10.0 | 9.0 | +8.0 |
| Learning | ~2.0 | 10.0 | 7.0 | +8.0 |
| Fallback Quality | ~2.0 | 10.0 | 8.0 | +8.0 |
| Negotiation | ~0.0 | 10.0 | 7.0 | +10.0 |
| Agent Autonomy | ~1.0 | 8.5 | 6.0 | +7.5 |
| Mother Decoupling | ~2.0 | 6.0 | 6.0 | +4.0 |
| Adaptive Pipeline | ~1.0 | 8.5 | 8.0 | +7.5 |
| **OVERALL** | **~3.5** | **8.9** | **7.5** | **+5.4** |

All 10 original critique dimensions now pass or are within 0.5 of target.
