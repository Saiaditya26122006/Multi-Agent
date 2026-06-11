# 🧠 Complete Intelligence System Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Intelligence Engine — 4-Step Reasoning](#intelligence-engine)
3. [Learning Engine — Pattern Extraction](#learning-engine)
4. [Quality Gates — Devil's Advocate & Council](#quality-gates)
5. [Coherence Auditor — Cross-Section Validation](#coherence-auditor)
6. [Agent Beliefs — Self-Awareness System](#agent-beliefs)
7. [How Each Agent Uses Intelligence](#agent-by-agent-breakdown)
8. [Intelligence Flow Diagram](#intelligence-flow)
9. [Configuration & Tuning](#configuration)

---

## System Overview

Your multi-agent system has **5 intelligence layers** that work together:

```
┌─────────────────────────────────────────────────────────────┐
│                        MOTHER AGENT                         │
│  (Orchestrator — runs final coherence audit & learning)     │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        │                           │
┌───────▼────────┐         ┌────────▼──────────┐
│ QUALITY GATES  │         │  CHILD AGENTS     │
│                │         │  (17 agents)      │
│ • Devil's Adv. │◄────────┤                   │
│ • Council (5)  │         │  Each uses:       │
└────────────────┘         │  • Intelligence   │
                           │  • Learning       │
                           │  • Beliefs        │
                           └───────────────────┘
```

---

## 1. Intelligence Engine — 4-Step Reasoning

**Location**: `agents/phase2/intelligence_engine.py`

### Purpose
Enforces **structured reasoning** for every child agent output. Prevents "I just wrote some text" outputs by forcing agents to:
1. Think before writing
2. Write with judgment coverage
3. Challenge their own thinking
4. Revise based on challenges

### The 4 Steps

```python
async def reason_and_produce(
    agent_role: str,
    input_data: dict,
    output_schema_prompt: str,
    cross_section_context: dict,
    reasoning_budget: int = 3,
    learning_context: str = "",
) -> tuple[dict, dict, dict]:
    """
    Step 1: DECOMPOSE → structured judgments
    Step 2: PRODUCE → draft addressing all judgments
    Step 3: CHALLENGE → structured critique
    Step 4: REVISE → fix with explicit checklist
    """
```

#### Step 1: DECOMPOSE (Extract Structured Judgments)

**What it does**: Asks LLM to analyze the problem and extract **structured judgments**.

**Format**:
```
JUDGMENT 1: [specific claim you must evaluate]
EVIDENCE FOR: [what supports this]
EVIDENCE AGAINST: [what undermines this]
CONFIDENCE: [high/medium/low]
KILL CONDITION: [what fact, if true, would invalidate this judgment]

JUDGMENT 2: ...

CAUSAL CHAIN: [A] → [B] → [C] (trace the logic)
FATAL ASSUMPTION: The ONE thing that kills this entire analysis if wrong.
```

**Example** (Opportunity Analyst):
```
JUDGMENT 1: This market is growing at 15% CAGR
EVIDENCE FOR: Gartner 2025 report page 42 cites 14.8% growth 2020-2025
EVIDENCE AGAINST: Report excludes institutional buyers which this business targets
CONFIDENCE: medium
KILL CONDITION: If institutional segment growth is flat/declining

FATAL ASSUMPTION: Universities have discretionary budget for this category
```

**Validation**:
- Extracts 3-5 judgments
- Parses structured fields (claim, evidence_for, evidence_against, kill_condition)
- Stores for coverage check in Step 2

---

#### Step 2: PRODUCE (Draft with Judgment Coverage)

**What it does**: Generates first draft that **MUST address every judgment from Step 1**.

**Prompt includes**:
```
YOUR OUTPUT MUST ADDRESS EACH OF THESE JUDGMENTS:
  - JUDGMENT 1: This market is growing at 15% CAGR
  - JUDGMENT 2: ...
  - JUDGMENT 3: ...

Rules:
- Every number must trace to a named assumption or input data
- Every strategic claim needs a "because" with specific evidence
- Confidence: LOW = "I'm guessing", MEDIUM = "reasonable inference", HIGH = "data-backed"
- Be specific: "$120k ARR" not "significant revenue"
```

**Enforcement**:
After draft is generated, runs `_check_judgment_coverage()`:
```python
def _check_judgment_coverage(draft_raw: str, judgments: list) -> dict:
    """Check which judgments are addressed in the draft."""
    draft_lower = draft_raw.lower()
    covered = 0
    missing = []

    for judgment in judgments:
        keywords = [w for w in claim.split() if len(w) > 4]
        matches = sum(1 for kw in keywords if kw in draft_lower)
        if matches / len(keywords) >= 0.4:
            covered += 1
        else:
            missing.append(judgment)

    return {"covered": covered, "missing": missing}
```

**If judgments are missing**: Calls `_produce_with_gaps()` to re-generate draft with explicit gap list.

---

#### Step 3: CHALLENGE (Structured Critique)

**What it does**: Acts as **Devil's Advocate** — finds specific problems in the draft.

**Skipped if**: `reasoning_budget < 3`

**Prompt**:
```
Find SPECIFIC problems.

For each problem found, use this format:

PROBLEM 1:
TYPE: [math_error | logical_gap | confidence_inflation | competitive_blindness | 
      unsupported_claim | generic_filler]
LOCATION: [which field or claim]
WHAT'S WRONG: [specific issue with numbers]
FIX NEEDED: [what the revision must do]

PROBLEM 2: ...

Only report REAL problems with specific evidence. Not stylistic preferences.
If the draft is solid, say "NO PROBLEMS FOUND".
```

**Example Output**:
```
PROBLEM 1:
TYPE: math_error
LOCATION: revenue_assumptions.volume_year1
WHAT'S WRONG: Claims 500 customers but conversion rate (2%) × traffic (10,000) = 200 customers, not 500
FIX NEEDED: Either increase traffic to 25,000 or increase conversion to 5%

PROBLEM 2:
TYPE: confidence_inflation
LOCATION: confidence_score
WHAT'S WRONG: Claims "high" confidence but 4 out of 7 assumptions are labelled "assumed" (no validation)
FIX NEEDED: Downgrade to "medium" or validate at least 2 more assumptions
```

**Validation**:
- Parses structured challenges
- Extracts type, location, problem, fix
- Returns empty list if "NO PROBLEMS FOUND"

---

#### Step 4: REVISE (Fix with Explicit Checklist)

**What it does**: Fixes problems from Step 3 with **explicit checklist**.

**Prompt**:
```
FIX THESE PROBLEMS (each MUST be addressed in your revision):
  [1] (math_error) Claims 500 customers but math shows 200 → FIX: recalculate or adjust assumptions
  [2] (confidence_inflation) Claims "high" but 4/7 assumptions "assumed" → FIX: downgrade confidence
  [3] ...

Rules:
- Fix math errors and logical inconsistencies
- Downgrade confidence_score if problems reveal genuine uncertainty
- Add valid problems to uncertainties list
- DO NOT water down analysis — keep conclusions sharp
```

**Enforcement After Revision**:
```python
unresolved = _check_challenge_resolution(revised_raw, challenges)
```

Checks if each challenge's keywords appear in the revised output. If not → unresolved.

**If unresolved challenges remain** AND `reasoning_budget >= 4`:
- Runs `_revise_targeted()` with only unresolved challenges
- Forces agent to either fix OR add to uncertainties

**Final Quality Enforcement**:
```python
if parsed and unresolved:
    parsed["confidence_score"] = "low"
    parsed["_unresolved_challenges"] = [c["problem"] for c in unresolved[:3]]
```

---

### Intelligence Engine Enforcement Mechanisms

#### 1. Judgment Coverage Enforcement
```python
coverage = self._check_judgment_coverage(draft_raw, judgments)
if coverage["missing"]:
    # Re-produce with gaps
    draft_raw = await self._produce_with_gaps(missing_judgments)
```

#### 2. Challenge Resolution Enforcement
```python
unresolved = self._check_challenge_resolution(final_raw, challenges)
if unresolved and reasoning_budget >= 4:
    # Second revision pass
    final_raw = await self._revise_targeted(unresolved)
```

#### 3. Confidence Downgrade Enforcement
```python
if parsed and unresolved:
    parsed["confidence_score"] = "low"  # Force downgrade
```

#### 4. Generic Filler Detection
```python
GENERIC_PHRASES = [
    "unique value proposition", "first-mover advantage",
    "cutting-edge", "best-in-class", "world-class",
    "leveraging synergies", "holistic approach",
    ...
]

generic_count = self._count_generic_phrases(parsed)
if generic_count >= 3:
    parsed["confidence_score"] = "low"
    parsed["_quality_warnings"].append(f"Contains {generic_count} generic phrases")
```

---

### Reasoning Budget System

Controls how deep the reasoning goes:

| Budget | Steps Run | When To Use |
|--------|-----------|-------------|
| **2** | Decompose + Produce only | Fast ungated sections (Environment, Operations) |
| **3** | Decompose + Produce + Challenge + Revise | Standard gated sections (Marketing, SWOT) |
| **4** | All steps + 2nd revision pass | Council revisions, high-stakes sections (Financial) |

**Code**:
```python
if reasoning_budget >= 3:
    challenge_raw, challenges = await self._challenge(draft)
    
if challenges and reasoning_budget >= 3:
    final_raw = await self._revise(draft, challenges)
    
    unresolved = self._check_challenge_resolution(final_raw, challenges)
    if unresolved and reasoning_budget >= 4:
        final_raw = await self._revise_targeted(final_raw, unresolved)
```

---

### Reasoning Trace Output

Every Intelligence Engine call returns a `reasoning_trace` dict:

```python
{
    "decomposition": "JUDGMENT 1: ...\nJUDGMENT 2: ...",
    "judgments_extracted": 4,
    "judgments_covered": 4,
    "judgments_missing": 0,
    "challenge": "PROBLEM 1: ...\nPROBLEM 2: ...",
    "challenges_found": 2,
    "challenges_resolved": 2,
    "challenges_unresolved": 0,
    "revision_count": 1,
    "revisions_applied": True,
    "reasoning_budget": 3,
    "generic_phrase_count": 0
}
```

This trace is:
- Stored in agent output for debugging
- Sent to Devil's Advocate for quality review
- Logged for learning engine pattern extraction

---

## 2. Learning Engine — Pattern Extraction

**Location**: `agents/phase2/learning_engine.py`

### Purpose
**Learns from failures** to prevent repeating mistakes. Goes beyond event logging to:
- Extract **structured failure patterns** (root cause, anti-pattern, positive pattern)
- Build **actionable learning context** for agents
- Track CEO preferences across runs
- Suggest prompt adjustments after recurring failures

### Event Recording

```python
class LearningEngine:
    def record_acceptance(session_id, section, confidence, assumptions_count, da_verdict):
        """Section was accepted by CEO without edits"""
        
    def record_rejection(session_id, section, reason, ceo_feedback):
        """Section was rejected — extract pattern from reason"""
        
    def record_edit(session_id, section, field, original, corrected):
        """CEO edited a field — infer what agent got wrong"""
```

---

### Pattern Extraction (No LLM Needed)

When rejection/edit occurs, automatically extracts structured pattern:

```python
def _extract_pattern_from_rejection(section_number, reason, ceo_feedback) -> dict:
    """Classify rejection into structured pattern WITHOUT LLM."""
    combined = reason.lower() + " " + ceo_feedback.lower()
    
    # Rule-based root cause detection
    if any(w in combined for w in ("math", "number", "calculation")):
        root_cause = "math_error"
    elif any(w in combined for w in ("generic", "vague", "filler")):
        root_cause = "generic_filler"
    elif any(w in combined for w in ("contradict", "inconsistent")):
        root_cause = "contradiction"
    # ... 8 root cause types total
    
    return {
        "root_cause": root_cause,
        "trigger_field": "",
        "anti_pattern": f"DO NOT: {reason[:200]}",
        "positive_pattern": f"INSTEAD: {ceo_feedback[:200]}",
        "source_event": "rejection"
    }
```

**Stored Pattern**:
```json
{
  "root_cause": "math_error",
  "trigger_field": "revenue_assumptions",
  "anti_pattern": "DO NOT: claim 500 customers when conversion math shows 200",
  "positive_pattern": "INSTEAD: show the math: 25,000 leads × 2% = 500 customers",
  "source_event": "rejection",
  "timestamp": "2026-06-11T10:30:00Z",
  "session_id": "abc123",
  "section": "8"
}
```

---

### Building Learning Context

When agent starts a task, Learning Engine builds **actionable learning context**:

```python
def build_learning_context(section_number: str) -> str:
    """Build actionable learning context — grouped by root cause."""
    patterns = self._get_extracted_patterns(section_number)
    edits = self._get_ceo_edit_patterns(section_number)
    
    lines = ["LEARNED PATTERNS (from past runs — follow these strictly):"]
    
    # Group by root cause
    by_cause = defaultdict(list)
    for p in patterns:
        by_cause[p["root_cause"]].append(p)
    
    for cause, instances in by_cause.items():
        lines.append(f"\n[{cause.upper()}] (occurred {len(instances)}x)")
        lines.append(f"  DO NOT: {latest['anti_pattern']}")
        lines.append(f"  INSTEAD: {latest['positive_pattern']}")
        lines.append(f"  WATCH FIELD: {latest['trigger_field']}")
    
    # CEO preferences from manual edits
    lines.append("\nCEO PREFERENCES (from manual edits):")
    for edit in edits[:5]:
        lines.append(f"  - Field '{edit['field']}': rejected '{edit['original'][:60]}', preferred '{edit['corrected'][:60]}'")
    
    # Recurring error warning
    recurring = self._get_recurring_errors(section_number)
    if recurring:
        lines.append("\nRECURRING ERRORS (fix these or confidence will be capped at 'low'):")
        for cause, count in recurring.items():
            lines.append(f"  - {cause}: failed {count}x")
    
    return "\n".join(lines)
```

**Example Output**:
```
LEARNED PATTERNS (from past runs — follow these strictly):

[MATH_ERROR] (occurred 3x)
  DO NOT: claim revenue without showing price × volume calculation
  INSTEAD: always show: price_per_unit × volume = total_revenue
  WATCH FIELD: revenue_assumptions

[GENERIC_FILLER] (occurred 2x)
  DO NOT: write "unique value proposition" without specifics
  INSTEAD: state what specifically is unique and why competitors can't copy it
  WATCH FIELD: competitive_advantages

CEO PREFERENCES (from manual edits):
  - Field 'market_size': rejected 'large and growing market', preferred '$2.5B TAM (Gartner 2025 report)'
  - Field 'pricing': rejected 'competitive pricing', preferred '$99/mo (20% below competitor A, 10% above B)'

RECURRING ERRORS (fix these or confidence will be capped at 'low'):
  - math_error: failed 3x
```

This context is **injected into Intelligence Engine** when agent runs:

```python
parsed, reasoning_trace, token_usage = await intelligence.reason_and_produce(
    agent_role="Marketing Strategy",
    input_data=input_data,
    output_schema_prompt=schema_prompt,
    learning_context=learning_engine.build_learning_context("8"),  # ← INJECTED HERE
    reasoning_budget=3
)
```

---

### Prompt Adjustment Suggestions

After 3+ failures of same type, Learning Engine suggests SYSTEM_PROMPT changes:

```python
def get_prompt_adjustment_suggestion(section_number: str) -> Optional[str]:
    """After 3+ failures of same type, suggest SYSTEM_PROMPT change."""
    patterns = self._get_extracted_patterns(section_number)
    
    cause_counts = Counter(p["root_cause"] for p in patterns)
    recurring = {k: v for k, v in cause_counts.items() if v >= 3}
    
    if not recurring:
        return None
    
    top_cause = max(recurring, key=recurring.get)
    latest = [p for p in patterns if p["root_cause"] == top_cause][-1]
    
    return (
        f"Section {section_number} has failed {recurring[top_cause]}x "
        f"due to '{top_cause}'. "
        f"Suggested: {latest['positive_pattern']}"
    )
```

**Example**:
```
Section 8 has failed 4x due to 'math_error'.
Suggested: INSTEAD: always show: price_per_unit × volume = total_revenue
```

This is **logged for human review** — system doesn't auto-modify prompts (too risky).

---

### Devil's Advocate Accuracy Tracking

Tracks whether Devil's Advocate challenges are correct:

```python
def record_da_accuracy(session_id, section, challenge_type, was_valid: bool):
    """Track whether Devil's Advocate challenges turned out to be correct."""

def get_da_accuracy_stats() -> dict:
    """Get accuracy statistics by challenge type."""
    # Returns: {"math_error": {"total": 10, "valid": 8, "accuracy": 0.8}, ...}
```

Used to **calibrate Devil's Advocate sensitivity** — if accuracy < 50% for a challenge type, that persona is too aggressive.

---

## 3. Quality Gates — Devil's Advocate & Council

### Devil's Advocate (Ungated Sections)

**Location**: `agents/phase2/devils_advocate.py`

**Purpose**: Pre-review for sections that don't go through Council. Finds:
- Logical gaps
- Overconfidence
- Unsupported claims
- Math errors
- Contradictions
- Survivorship bias

**Process**:
1. Child agent sends output → Devil's Advocate
2. Devil's Advocate reviews with structured critique
3. Returns verdict: `pass` | `revise` | `reject` | `escalate`
4. If `pass` → forward to Mother
5. If `revise` → send revision instructions back to child
6. If `escalate` → quality gate failure (see Risk 3 fix)

**SYSTEM_PROMPT**:
```python
"You are the Devil's Advocate in a multi-agent business plan system.
Your job: ruthlessly challenge every section output before it gets delivered to the CEO.

Rules:
- Challenge SPECIFIC claims — not vague 'this could be better'
- Every challenge must cite the exact text or number being questioned
- 'Overconfidence' means section claims high/medium confidence but evidence is weak
- 'Logical gap' means A doesn't actually lead to B even though section claims it does

Verdict rules:
- 'pass' — fewer than 2 issues total, none high-severity
- 'revise' — 2+ medium issues or 1+ high-severity issue that can be fixed
- 'reject' — multiple high-severity issues indicating fundamental problems
- 'escalate' — quality gate system failure (use only if LLM fails)"
```

**Output Schema**:
```python
class DevilsAdvocateOutput(BaseModel):
    verdict: Literal["pass", "revise", "reject", "escalate"]
    challenges: List[Challenge]
    confidence_assessment: Literal["honest", "inflated", "deflated", "unknown"]
    recommended_confidence: Literal["high", "medium", "low"]
    assumptions_grade: Literal["well_sourced", "mixed", "mostly_unsupported", "unknown"]
    overall_reasoning_quality: Literal["strong", "adequate", "weak", "unknown"]
    summary: str  # One-paragraph for Mother Agent
```

**Challenge Structure**:
```python
class Challenge(BaseModel):
    claim: str
    challenge_type: Literal["logical_gap", "overconfidence", "unsupported", 
                            "math_error", "contradiction", "survivorship_bias", 
                            "system_failure"]
    severity: Literal["high", "medium", "low"]
    explanation: str  # min 20 chars
    suggested_fix: str
    section_reference: Optional[str]  # If contradiction
```

---

### Council Agent (Gated Sections)

**Location**: `agents/phase2/council_agent.py`

**Purpose**: **5-persona deliberation** for high-stakes sections (SWOT, Marketing, Financial, Executive Summary).

**Gated Sections** (from `config/phase2/council_config.py`):
```python
COUNCIL_GATED_SECTIONS = ["5", "8", "12", "executive_summary"]
```

**The 5 Personas**:

| Persona | Icon | Role | What They Check |
|---------|------|------|-----------------|
| **Skeptic** | ⚠️ | Find flaws | Weak evidence, unsupported claims, optimism bias, circular reasoning |
| **Architect** | 🏗️ | Check structure | Contradictions, gaps, dependencies, confidence accuracy |
| **Visionary** | 💡 | Think bigger | Adjacent opportunities, 10x version, strategic leverage, network effects |
| **Stranger** | ❓ | Test clarity | Jargon, logic jumps, numbers without derivation, unclear terms |
| **Operator** | 🔧 | Check feasibility | Timeline realistic? Team capacity? Hidden dependencies? First bottleneck? |

**Process**:
1. Child agent sends output → Council
2. Council spawns 5 personas **in parallel** (all run on Haiku for speed)
3. Each persona returns structured critique:
   ```json
   {
     "top_finding": "specific issue found",
     "severity": "critical"|"minor"|"none",
     "detail": "explanation",
     "persona-specific-field": "..."
   }
   ```
4. **Synthesizer** (runs on Sonnet) combines 5 critiques into verdict:
   ```python
   RULES:
   - If ANY review has severity "critical": verdict is "revise"
   - If 3+ reviews have severity "minor": verdict is "revise"
   - Otherwise: verdict is "pass"
   - Score: 10 minus (2 per critical, 0.5 per minor)
   ```
5. If `revise` and attempt < MAX_COUNCIL_REVISIONS (2):
   - Send revision instructions back to child
   - **Notify Mother** to reset task TTL (Risk 1 fix)
   - Child re-processes with revision feedback
6. If `pass` OR max revisions hit:
   - Forward to Mother

**Revision Loop**:
```
Child Agent → Council → 5 Personas (parallel) → Synthesizer
    ▲                                                │
    │                                                │
    └───────────── revise (attempt 1) ──────────────┘
    ▲                                                │
    │                                                │
    └───────────── revise (attempt 2) ──────────────┘
    ▲                                                │
    │                                                │
    └───────── max revisions hit → PASS ─────────────┘
```

**Max Revisions Hit**:
```python
if attempt >= MAX_COUNCIL_REVISIONS:
    logger.warning(
        "[CouncilAgent] Section %s hit max revisions — passing with warnings",
        section_number,
    )
    self._notify_alex_escalate(session_id, section_name, verdict)
    await self._forward_to_mother(...)  # Pass through with _council_warnings
```

**Council Notifications to CEO** (via Telegram):
```
🔍 Council is reviewing: Marketing Strategy

📋 Council Review: Marketing Strategy
┊ ⚠️ Skeptic: CAC calculation assumes 2% conversion but no data cited [MINOR]
┊ 🏗️ Architect: Revenue figure conflicts with Section 1 TAM-SAM-SOM [CRITICAL]
┊ 💡 Visionary: No expansion revenue model — missing 2x opportunity [MINOR]
┊ ❓ Stranger: "LTV" used without defining calculation method [NONE]
┊ 🔧 Operator: Timeline shows break-even month 18 but headcount plan only covers 12 months [CRITICAL]

🔄 Verdict: REVISE (2 critical issues)

---

[After child revision]

✅ Council: Marketing Strategy (Revised) — Score 8.5/10
Improvements:
• Added TAM-SAM-SOM cross-check with Section 1
• Extended headcount plan to 24 months
• Added LTV calculation methodology
```

---

## 4. Coherence Auditor — Cross-Section Validation

**Location**: `agents/phase2/coherence_auditor.py`

**Purpose**: **Deterministic cross-section consistency checks** after all sections are complete.

Unlike LLM-based audits, this runs **fast programmatic checks**:

### Check 1: Revenue Consistency

Compares:
- Section 8 (Marketing) `revenue_assumptions.price_per_unit × volume_year1`
- Section 12 (Financial) `three_statement_model.revenue_year1`

```python
expected_revenue = mkt_price × mkt_volume_y1
actual_revenue = financial_revenue_y1
deviation = abs(actual - expected) / expected

if deviation > 0.2:  # 20% threshold
    contradictions.append({
        "type": "revenue_mismatch",
        "description": f"Marketing implies ${expected:,.0f}/yr but Financial shows ${actual:,.0f}/yr ({deviation:.0%} deviation)",
        "severity": "high"
    })
```

### Check 2: Confidence Chain Validation

Ensures no section claims higher confidence than its upstream dependencies:

```python
dependency_chains = [
    ("1", "8"),   # Opportunity → Marketing
    ("1", "5"),   # Opportunity → SWOT
    ("5", "8"),   # SWOT → Marketing
    ("8", "12"),  # Marketing → Financial
    ("12", "13"), # Financial → Launch
]

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

for upstream_sec, downstream_sec in dependency_chains:
    upstream_conf = outputs[upstream_sec]["confidence_score"]
    downstream_conf = outputs[downstream_sec]["confidence_score"]
    
    if CONFIDENCE_RANK[downstream_conf] > CONFIDENCE_RANK[upstream_conf]:
        warnings.append(
            f"Section {downstream_sec} claims '{downstream_conf}' but depends on "
            f"Section {upstream_sec} which is '{upstream_conf}'"
        )
```

**Example**: If Opportunity (Section 1) has `confidence: "low"` but Marketing (Section 8) claims `confidence: "high"`, that's dishonest — Marketing can't be more confident than its inputs.

### Check 3: Timeline Alignment

Compares:
- Section 12 `break_even_analysis.break_even_month`
- Section 13 `launch_programme.duration_months`

```python
if launch_duration > break_even_month:
    contradictions.append({
        "type": "timeline_conflict",
        "description": f"Launch programme spans {launch_months} months but break-even is at month {be_month} — launch ends after break-even which is unusual",
        "severity": "medium"
    })
```

### Audit Result

```python
@dataclass
class AuditResult:
    contradictions: list[dict]  # High/medium severity issues
    warnings: list[str]         # Low severity issues
    consistency_score: float    # 0.0 to 1.0
```

**Scoring**:
```python
score = 1.0
score -= len(contradictions) * 0.15
score -= len(warnings) * 0.05
score = max(0.0, min(1.0, score))
```

**Mother Agent uses this**:
```python
audit_result = coherence_auditor.audit(prior_outputs)

if audit_result.consistency_score < 0.7:
    # Send contradictions to CEO for review
    # Optionally run LLM-based deep audit
```

---

## 5. Agent Beliefs — Self-Awareness System

**Location**: `agents/phase2/agent_beliefs.py`

**Purpose**: Agents maintain **beliefs** about their environment and auto-invalidate beliefs when contradicted.

### Belief Structure

```python
@dataclass
class Belief:
    statement: str               # "TAM for academic software is $50M"
    confidence: float            # 0.0 to 1.0
    source: str                  # "Gartner 2025 report"
    timestamp: datetime
    invalidated: bool = False
    invalidation_reason: Optional[str] = None
```

### AgentBeliefStore

```python
class AgentBeliefStore:
    def assert_belief(self, statement: str, confidence: float, source: str):
        """Agent asserts a new belief."""
        
    def invalidate_belief(self, statement: str, reason: str):
        """Mark belief as no longer valid."""
        
    def get_active_beliefs(self) -> list[Belief]:
        """Get all non-invalidated beliefs."""
        
    def check_contradiction(self, new_statement: str, new_confidence: float) -> Optional[Belief]:
        """Check if new statement contradicts existing beliefs."""
```

### Auto-Invalidation Example

**Scenario**: Opportunity Agent (Section 1) asserts:
```python
beliefs.assert_belief(
    statement="TAM for academic software is $50M",
    confidence=0.7,
    source="Gartner 2025 report page 42"
)
```

Later, Marketing Agent (Section 8) receives this belief in cross-section context and asserts:
```python
beliefs.assert_belief(
    statement="TAM for academic software is $2.5B",
    confidence=0.9,
    source="IDC 2025 market analysis"
)

# Check contradiction
contradiction = beliefs.check_contradiction(
    new_statement="TAM for academic software is $2.5B",
    new_confidence=0.9
)

if contradiction:
    beliefs.invalidate_belief(
        statement=contradiction.statement,
        reason=f"Contradicted by higher-confidence source: IDC 2025 ($2.5B > $50M)"
    )
```

**Usage in Agents**:
- Before using cross-section data, check if it contradicts agent's own beliefs
- If contradiction found, flag in output as uncertainty
- Don't silently accept contradictory data — make it explicit

---

## 6. How Each Agent Uses Intelligence

### Agent Intelligence Integration (BaseChildAgent)

**All 17 child agents** inherit from `BaseChildAgent` which integrates:

```python
class BaseChildAgent(Agent, ABC):
    def __init__(self, jid, password):
        self.intelligence = IntelligenceEngine(bedrock, model_id)
        self.beliefs = AgentBeliefStore(agent_name, redis)
        self.learning = LearningEngine(redis, supabase)  # via Mother
```

### Standard Request Flow

```python
async def handle_request(self, task_id, session_id, pipeline_run_id, content):
    # 1. Validate input
    validated_input = INPUT_SCHEMA(**input_package)
    
    # 2. Get learning context from past runs
    learning_context = learning_engine.build_learning_context(SECTION_NUMBER)
    
    # 3. Run Intelligence Engine (4-step reasoning)
    parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
        agent_role=AGENT_ROLE,
        input_data=self._build_ie_input_data(input_package),
        output_schema_prompt=self._build_schema_prompt(),
        cross_section_context=input_package.get("cross_section_context"),
        reasoning_budget=self.reasoning_budget(revision_required),
        learning_context=learning_context
    )
    
    # 4. If Intelligence Engine fails, fallback to direct LLM
    if not parsed:
        llm_response = await self._call_llm(user_message)
        parsed = self._parse_llm_response(llm_response)
    
    # 5. Validate output schema
    validated_output = OUTPUT_SCHEMA(**parsed)
    
    # 6. Check for contradictions with beliefs
    self._check_belief_contradictions(validated_output)
    
    # 7. Send to quality gate (Devil's Advocate OR Council)
    if SECTION_NUMBER in COUNCIL_GATED_SECTIONS:
        await self._send_to_council(validated_output)
    else:
        await self._send_inform(validated_output)  # Mother validates via Devil's Advocate
```

### Agent-by-Agent Intelligence Usage

| Agent | Reasoning Budget | Quality Gate | Special Intelligence Features |
|-------|------------------|--------------|------------------------------|
| **Opportunity Analyst** (1) | 3 | Devil's Advocate | TAM-SAM-SOM validation, capture rate checks |
| **Entrepreneur Team** (2) | 2 | Devil's Advocate | Team credibility scoring, gap identification |
| **Environment Research** (3) | 2 | Devil's Advocate | PEST + Porter's 5 Forces cross-validation |
| **Organisation Designer** (4) | 2 | Devil's Advocate | Capability gap vs team gap consistency |
| **SWOT Synthesizer** (5) | 4 | **Council** | Cross-validates Sections 1,3,4; strategic implications |
| **R&D Technology** (6) | 2 | Devil's Advocate | TRL scoring, IP defensibility assessment |
| **Alliances** (7) | 2 | Devil's Advocate | Partnership value exchange validation |
| **Marketing Strategy** (8) | 4 | **Council** | **Magic Ratio Guardrail** (LTV:CAC ≥ 3:1), unit economics validation |
| **Quality Management** (9) | 2 | Devil's Advocate | Quality metrics feasibility check |
| **Operations** (10) | 2 | Devil's Advocate | Cost structure validation (CostStructure schema) |
| **HR Plan** (11) | 3 | Devil's Advocate | Headcount plan validation (MonthlyHeadcount schema), capacity matching |
| **Financial Modelling** (12) | 4 | **Council** | SimPy integration, 3-statement model validation, break-even sensitivity |
| **Launch & Contingency** (13) | 2 | Devil's Advocate | Timeline vs resources feasibility |
| **Exit Strategy** (14) | 3 | Devil's Advocate | Cap table math validation, investor return realism |
| **Summary Agent** (15) | 2 | **Council** | Cross-section synthesis, exec summary coherence |

### Reasoning Budget Per Agent

```python
def reasoning_budget(self, revision_required: bool) -> int:
    """Return reasoning budget based on agent type and revision status."""
    if revision_required:
        return 4  # Council revision = deepest reasoning
    
    if self.SECTION_NUMBER in COUNCIL_GATED_SECTIONS:
        return 3  # Gated sections get standard 4-step
    
    return 2  # Ungated sections get fast 2-step (decompose + produce only)
```

**Why different budgets?**
- **Budget 2**: Fast sections don't need challenge step (Environment, Operations) — decompose + produce is enough
- **Budget 3**: Standard gated sections (Marketing, SWOT) — full 4-step reasoning
- **Budget 4**: Council revisions need 2nd revision pass to resolve all challenges

---

## 7. Intelligence Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              USER REQUEST                               │
│                    (CEO: "Generate business plan")                      │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          MOTHER AGENT                                   │
│  • Plans execution groups                                               │
│  • Loads Learning Engine context                                        │
│  • Dispatches tasks to child agents                                     │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│    CHILD AGENT (x17)     │    │    CHILD AGENT (x17)     │
│                          │    │                          │
│ 1. Validate Input        │    │ (All agents run in       │
│ 2. Get Learning Context  │    │  parallel within group)  │
│    ├─ Past failures      │    │                          │
│    ├─ CEO preferences    │    └──────────────────────────┘
│    └─ Recurring patterns │
│                          │
│ 3. INTELLIGENCE ENGINE   │
│    ┌──────────────────┐  │
│    │ STEP 1: DECOMPOSE│  │
│    │ Extract 3-5       │  │
│    │ judgments with    │  │
│    │ evidence          │  │
│    └────────┬─────────┘  │
│             │            │
│    ┌────────▼─────────┐  │
│    │ STEP 2: PRODUCE  │  │
│    │ Draft addressing │  │
│    │ all judgments    │  │
│    └────────┬─────────┘  │
│             │            │
│    ┌────────▼─────────┐  │
│    │ Coverage Check   │  │
│    │ Missing? Re-gen  │  │
│    └────────┬─────────┘  │
│             │            │
│    ┌────────▼─────────┐  │
│    │ STEP 3: CHALLENGE│  │
│    │ Structured       │  │
│    │ critique         │  │
│    └────────┬─────────┘  │
│             │            │
│    ┌────────▼─────────┐  │
│    │ STEP 4: REVISE   │  │
│    │ Fix with         │  │
│    │ checklist        │  │
│    └────────┬─────────┘  │
│             │            │
│    ┌────────▼─────────┐  │
│    │ Resolution Check │  │
│    │ Unresolved? 2nd  │  │
│    │ revision         │  │
│    └────────┬─────────┘  │
│             │            │
│ 4. Validate Output       │
│ 5. Check Beliefs         │
│                          │
│ 6. Send to Quality Gate  │
└──────────┬───────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         QUALITY GATES                                   │
│                                                                         │
│  ┌────────────────────┐              ┌─────────────────────────────┐  │
│  │ DEVIL'S ADVOCATE   │              │     COUNCIL AGENT           │  │
│  │                    │              │                             │  │
│  │ For ungated        │              │ For gated sections:         │  │
│  │ sections           │              │ • Section 5 (SWOT)          │  │
│  │                    │              │ • Section 8 (Marketing)     │  │
│  │ Checks:            │              │ • Section 12 (Financial)    │  │
│  │ • Logical gaps     │              │ • Executive Summary         │  │
│  │ • Overconfidence   │              │                             │  │
│  │ • Math errors      │              │ 5 Personas (parallel):      │  │
│  │ • Contradictions   │              │ ┌─────────────────────────┐ │  │
│  │                    │              │ │ ⚠️  Skeptic (flaws)     │ │  │
│  │ Verdict:           │              │ │ 🏗️  Architect (struct)  │ │  │
│  │ pass/revise/       │              │ │ 💡 Visionary (bigger)   │ │  │
│  │ reject/escalate    │              │ │ ❓ Stranger (clarity)   │ │  │
│  └────────┬───────────┘              │ │ 🔧 Operator (feasible) │ │  │
│           │                          │ └───────────┬─────────────┘ │  │
│           │                          │             │               │  │
│           │                          │    ┌────────▼──────────┐    │  │
│           │                          │    │ Synthesizer       │    │  │
│           │                          │    │ (Sonnet)          │    │  │
│           │                          │    │                   │    │  │
│           │                          │    │ Combines 5        │    │  │
│           │                          │    │ critiques into    │    │  │
│           │                          │    │ verdict + score   │    │  │
│           │                          │    └────────┬──────────┘    │  │
│           │                          │             │               │  │
│           │                          │    If revise & attempt < 2: │  │
│           │                          │    ┌────────▼──────────┐    │  │
│           │                          │    │ Send back to      │    │  │
│           │                          │    │ child agent       │    │  │
│           │                          │    │ + notify Mother   │    │  │
│           │                          │    └────────┬──────────┘    │  │
│           │                          │             │               │  │
│           │                          │             ▼               │  │
│           │                          │    (Child re-processes      │  │
│           │                          │     with revision feedback) │  │
│           │                          └─────────────────────────────┘  │
│           │                                      │                    │
│           └──────────────┬───────────────────────┘                    │
│                          │                                            │
│                 If pass OR max revisions hit                          │
│                          │                                            │
└──────────────────────────┼────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      MOTHER AGENT (Integration)                         │
│                                                                         │
│  1. Receive approved output from quality gate                          │
│  2. Store in Redis + Supabase                                          │
│  3. Run COHERENCE AUDITOR (after all sections complete):               │
│     ┌─────────────────────────────────────────────────────────────┐   │
│     │ Coherence Auditor (Deterministic)                           │   │
│     │ • Revenue consistency check (Sect 8 vs 12)                  │   │
│     │ • Confidence chain validation (no high from low)            │   │
│     │ • Timeline alignment (break-even vs launch)                 │   │
│     │ → Returns: contradictions, warnings, consistency_score      │   │
│     └─────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  4. If consistency_score < 0.7: notify CEO of contradictions           │
│  5. Record outcomes in LEARNING ENGINE:                                │
│     • If section accepted → record_acceptance()                        │
│     • If section rejected → record_rejection() + extract pattern       │
│     • If section edited → record_edit() + extract pattern              │
│  6. Compile final document                                             │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         FINAL OUTPUT                                    │
│                                                                         │
│  • Complete 14-section business plan (.docx)                           │
│  • Reasoning traces for each section                                   │
│  • Quality scores (Devil's Advocate / Council)                         │
│  • Coherence audit report                                              │
│  • Learning patterns extracted for next run                            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Configuration & Tuning

### Intelligence Engine Configuration

**File**: `agents/phase2/intelligence_engine.py` (constants at top)

```python
CAUSAL_MARKERS = [
    "because", "therefore", "since", "given that", "implies",
    "leads to", "results in", "causes", "driven by", "due to",
]

GENERIC_PHRASES = [
    "unique value proposition", "first-mover advantage",
    "cutting-edge", "best-in-class", "world-class",
    "leveraging synergies", "holistic approach",
]
```

**Tuning**:
- Add more `CAUSAL_MARKERS` if agents use domain-specific causal language
- Add more `GENERIC_PHRASES` if specific industry jargon should be flagged

### Council Configuration

**File**: `config/phase2/council_config.py`

```python
MAX_COUNCIL_REVISIONS = 2  # How many revision loops before forcing pass

COUNCIL_GATED_SECTIONS = ["5", "8", "12", "executive_summary"]  # Which sections go through Council

PERSONA_MODEL = "claude-haiku"      # Model for 5 personas (fast, parallel)
SYNTHESIZER_MODEL = "claude-sonnet"  # Model for synthesis (strategic reasoning)
```

**Tuning**:
- Increase `MAX_COUNCIL_REVISIONS` to 3 if quality is more important than speed
- Add more sections to `COUNCIL_GATED_SECTIONS` for stricter quality
- Change `PERSONA_MODEL` to `claude-sonnet` for higher-quality persona critiques (slower, more expensive)

### Learning Engine Configuration

**File**: `agents/phase2/learning_engine.py` (constants at top)

```python
ROOT_CAUSE_TYPES = [
    "unsupported_claim",
    "math_error",
    "market_ignorance",
    "generic_filler",
    "contradiction",
    "overconfidence",
    "missing_evidence",
    "wrong_assumption",
    "formatting_error",
]
```

**Tuning**:
- Add domain-specific root causes (e.g., "regulatory_blindness" for healthcare, "scalability_unrealism" for tech)
- Threshold for "recurring error" is 3 — lower to 2 for stricter learning

### Coherence Auditor Configuration

**File**: `agents/phase2/coherence_auditor.py` (thresholds in methods)

```python
# Revenue mismatch threshold
if deviation > 0.2:  # 20%
    # Flag as contradiction

# Scoring penalties
score = 1.0
score -= len(contradictions) * 0.15  # -0.15 per contradiction
score -= len(warnings) * 0.05        # -0.05 per warning
```

**Tuning**:
- Tighten `deviation > 0.2` to `0.1` for stricter revenue consistency
- Increase contradiction penalty to `-0.20` if contradictions are critical

---

## Summary: Intelligence System in One Page

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INTELLIGENCE SYSTEM LAYERS                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  LAYER 1: INTELLIGENCE ENGINE (4-Step Reasoning)                   │
│  ├─ Decompose → Extract structured judgments                       │
│  ├─ Produce → Draft addressing all judgments                       │
│  ├─ Challenge → Structured critique                                │
│  └─ Revise → Fix with explicit checklist                           │
│                                                                     │
│  LAYER 2: LEARNING ENGINE (Pattern Extraction)                     │
│  ├─ Record: acceptance, rejection, edit events                     │
│  ├─ Extract: root cause, anti-pattern, positive pattern            │
│  ├─ Build Context: grouped by root cause, CEO preferences          │
│  └─ Suggest: prompt adjustments after 3+ failures                  │
│                                                                     │
│  LAYER 3: QUALITY GATES (Devil's Advocate & Council)               │
│  ├─ Devil's Advocate: ungated sections (11 agents)                 │
│  │   └─ Verdict: pass | revise | reject | escalate                │
│  └─ Council: gated sections (4 agents)                             │
│      ├─ 5 Personas: Skeptic, Architect, Visionary, Stranger, Op   │
│      ├─ Synthesizer: combines into verdict + score                 │
│      └─ Revision loop: max 2 attempts                              │
│                                                                     │
│  LAYER 4: COHERENCE AUDITOR (Cross-Section Validation)             │
│  ├─ Revenue consistency: Section 8 vs 12                           │
│  ├─ Confidence chain: no high from low                             │
│  ├─ Timeline alignment: break-even vs launch                       │
│  └─ Consistency score: 0.0 to 1.0                                  │
│                                                                     │
│  LAYER 5: AGENT BELIEFS (Self-Awareness)                           │
│  ├─ Assert beliefs with confidence + source                        │
│  ├─ Check contradictions with cross-section data                   │
│  └─ Auto-invalidate when contradicted by higher confidence         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

INTEGRATION:
Every child agent (17 total) inherits BaseChildAgent which:
  1. Runs Intelligence Engine with learning context
  2. Validates output against beliefs
  3. Sends to quality gate (Devil's Advocate OR Council)
  4. Mother records outcomes in Learning Engine
  5. Mother runs Coherence Auditor after all sections complete
```

---

**Total Intelligence Components**: 25
- 1 Intelligence Engine
- 1 Learning Engine
- 2 Quality Gates (Devil's Advocate + Council)
- 1 Coherence Auditor
- 1 Agent Belief System
- 17 Child Agents (all integrated)
- 1 Mother Agent (orchestrator)
- 1 Document Compiler

**Token Cost Per Section** (approximate):
- Reasoning budget 2: ~8K tokens (decompose + produce)
- Reasoning budget 3: ~12K tokens (+ challenge + revise)
- Reasoning budget 4: ~16K tokens (+ 2nd revision)
- Devil's Advocate: ~3K tokens
- Council: ~8K tokens (5 personas + synthesizer)

**End-to-End Intelligence Flow Time**:
- Ungated section: 20-30 seconds (reasoning + Devil's Advocate)
- Council-gated section: 40-60 seconds (reasoning + Council 5 personas)
- Full 14-section plan: ~15-20 minutes (parallel execution in 4 groups)

---

**This is your complete intelligence system.** Every agent uses all 5 layers working together to produce rigorous, validated, cross-checked business plan sections.
