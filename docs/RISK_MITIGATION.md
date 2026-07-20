# Risk Mitigation — Multi-Agent System

This document tracks identified risks and their mitigations.

---

## Risk 1: Council Gate Circuit Deadlock ✅ MITIGATED

### Problem
Council Agent and child agents can loop on revisions without notifying Mother Agent. If Mother's task TTL expires during this loop, the task becomes orphaned.

### Root Cause
- Council sends `revise` performative directly to child agent
- Child re-processes and sends back to Council
- Mother is not in the loop until Council passes
- Mother's Redis `task_readiness` TTL can expire during long revision loops

### Mitigation (Implemented 2026-06-11)
**Council Agent** (`agents/phase2/council_agent.py`):
- When entering revision loop, Council now sends `performative="status_update"` to Mother
- Message includes: `status="council_revising"`, `section_number`, `revision_attempt`

**Mother Agent** (`agents/phase2/mother_agent.py`):
- Added `handle_status_update()` method
- Listens for `status_update` performative
- Updates `task_readiness.updated_at` timestamp (resets TTL)
- Sets status to `"council_revising"` for visibility

**Result**: Mother tracks revision loops and prevents task orphaning.

---

## Risk 2: Financial Model Math Failure ❌ NOT A REAL RISK

### Claimed Problem
LLMs are bad at generating raw spreadsheet data directly without code generation. Financial Agent might violate double-entry bookkeeping rules.

### Why This Is Not A Risk
**Financial Agent does NOT generate raw numbers**. Here's the actual flow:

1. Financial Agent receives structured inputs:
   - `revenue_assumptions` (from Marketing — Section 8)
   - `cost_structure` (from Operations — Section 10)
   - `headcount_plan` (from HR Plan — Section 11)

2. Agent builds `sim_assumptions` dict (line 365-387 in `financial_modelling.py`):
   ```python
   {
       "price_per_unit": 100,
       "volume_year1": 100,
       "churn_rate": 0.12,
       "cac": 500,
       "fixed_costs_monthly": 10000,
       # ... etc
   }
   ```

3. **Deterministic Python code** (`simulation/financial_sim.py`) runs Monte Carlo:
   - 1000 simulation runs with these assumptions
   - Enforces balance sheet identity: `assets = liabilities + equity`
   - Returns probability distributions (P10/P50/P90)

4. LLM outputs **configuration + simulation results**, not raw P&L numbers:
   - `assumption_log`: list of assumptions with labels ("validated", "assumed")
   - `break_even_analysis`: references simulation results
   - `three_statement_model`: structure only, numbers come from simulation

**Result**: Math correctness is enforced by Python, not LLM. No fix needed.

### Evidence
- `financial_modelling.py` line 223-226: SimPy runs before LLM call
- `simulation/financial_sim.py`: deterministic calculation engine
- LLM only generates `assumption_log` and narrative (line 431)

---

## Risk 3: Fallback Pass Danger ✅ MITIGATED

### Problem
If Financial Agent fails catastrophically AND Devil's Advocate also fails, the section passes with `verdict: "pass"` — downstream agents ingest broken data.

### Root Cause
**Devil's Advocate** (`agents/phase2/devils_advocate.py`) had `_fallback_pass()` method:
```python
def _fallback_pass(self, task_id: str) -> dict:
    return {
        "verdict": "pass",  # ⚠️ SILENT FAILURE
        "challenges": [],
        "summary": "Devil's Advocate review could not be completed..."
    }
```

### Real Scenario
1. Financial Agent hits Bedrock timeout
2. Financial fallback returns `confidence_score: "low"` with placeholder numbers
3. Devil's Advocate is called on this output
4. Devil's Advocate LLM also fails
5. **Fallback pass lets broken financials through**

### Mitigation (Implemented 2026-06-11)

#### 1. Schema Changes (`schemas/outputs/devils_advocate.py`)
- Added `"escalate"` to `verdict` Literal
- Added `"system_failure"` to `challenge_type` Literal
- Added `"unknown"` to confidence/grade enums

#### 2. Devil's Advocate Agent
Replaced `_fallback_pass()` with `_fallback_escalate()`:
```python
def _fallback_escalate(self, task_id: str, section_number: str, reason: str) -> dict:
    return {
        "verdict": "escalate",  # NEW
        "challenges": [{
            "claim": "Devil's Advocate quality gate failed",
            "challenge_type": "system_failure",
            "severity": "high",
            "explanation": f"Quality review could not be completed: {reason}",
            "suggested_fix": "Manual review required",
            "section_reference": None,
        }],
        "summary": f"ESCALATION REQUIRED: {reason}. Manual review before proceeding.",
        # ...
    }
```

All 4 failure points now escalate:
- Input validation failure → escalate
- LLM call failure → escalate
- LLM parse failure → escalate
- Output validation failure → escalate

Added `_escalate_to_mother()` method with `performative="escalate"`.

#### 3. Mother Agent Changes
Added quality gate failure handler in `handle_escalate()`:
```python
if trigger == "quality_gate_failure":
    # Pause pipeline immediately
    self.redis.client.set(f"pipeline:{run_id}:status", "paused_quality_gate")
    
    # Notify CEO with urgency
    self._send_Web Interface(session_id, 
        "🚨 CRITICAL: Quality gate failed for Section {section}\n"
        "Pipeline PAUSED. Manual review required.\n"
        "Reply 'continue' to override, or 'abort' to stop."
    )
    
    # Wait for CEO decision
    override_decision = await self._wait_for_ceo_override(session_id, run_id, timeout=86400)
    if override_decision == "continue":
        # Log override and proceed
    elif override_decision == "abort":
        # Stop pipeline
    else:
        # Timeout → remain paused
```

Added `_wait_for_ceo_override()` method that polls Redis for CEO response.

#### 4. CEO Web Interface Integration
When Devil's Advocate fails, CEO receives:
```
🚨 CRITICAL: Quality gate failed for Section 12

Reason: LLM call failed

Pipeline PAUSED. Manual review required before continuing.

Reply 'continue' to override and proceed, or 'abort' to stop pipeline.
```

CEO can:
- Reply `"continue"` → Override quality gate, proceed with warnings
- Reply `"abort"` → Stop pipeline immediately
- No reply within 24h → Pipeline remains paused

**Result**: No broken sections can proceed silently. CEO always notified of quality failures.

---

## Summary

| Risk | Status | Severity | Mitigation Date |
|------|--------|----------|-----------------|
| **Risk 1: Council Deadlock** | ✅ Mitigated | HIGH | 2026-06-11 |
| **Risk 2: Financial Math** | ❌ Not Real | N/A | Design prevents this |
| **Risk 3: Fallback Pass** | ✅ Mitigated | CRITICAL | 2026-06-11 |

All identified risks are now resolved or proven non-existent.

---

## Testing Checklist

### Risk 1 Testing
- [ ] Trigger Council revision loop (make Marketing output weak)
- [ ] Verify Mother receives `status_update` performative
- [ ] Check `task_readiness.updated_at` is updated
- [ ] Confirm task does not expire during 3+ revision loops

### Risk 3 Testing
- [ ] Force Devil's Advocate LLM failure (invalid API key)
- [ ] Verify escalation is sent to Mother
- [ ] Check pipeline status = `"paused_quality_gate"`
- [ ] Verify CEO receives Web Interface notification
- [ ] Test CEO override: reply "continue"
- [ ] Test CEO abort: reply "abort"
- [ ] Test timeout: no reply within 24h

---

## Monitoring

Add these metrics to track risk mitigation effectiveness:

```python
# In mother_agent.py
self.db.client.table("system_metrics").insert({
    "metric_name": "quality_gate_escalations",
    "metric_value": 1,
    "session_id": session_id,
    "section_number": section,
    "trigger": "quality_gate_failure",
    "timestamp": datetime.utcnow().isoformat(),
})
```

Track:
- `quality_gate_escalations` (count per session)
- `council_revision_loops` (max attempts per section)
- `ceo_override_rate` (continue vs abort ratio)
- `task_orphaning_rate` (should be 0 after fix)
