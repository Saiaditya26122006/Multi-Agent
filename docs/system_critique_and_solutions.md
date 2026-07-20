# Multi-Agent System: Intelligence & Communication Critique + Solutions

**Date:** 2026-05-29
**Scope:** All agents in `agents/phase2/`, communication layer, reasoning depth, learning mechanisms

---

## CRITIQUE 1: Individual Agent Intelligence Is Near Zero

### Problem

Every child agent's SYSTEM_PROMPT is a JSON template instruction, not a reasoning framework. Agents are told *what fields to return*, not *how to think*.

**Example — Opportunity Analyst:**
```
"Return ONLY valid JSON with these fields: opportunity_description (min 50 chars),
competitive_strategy (min 30 chars)..."
```

No guidance on:
- How to evaluate market timing or defensibility
- How to validate assumptions against evidence
- What distinguishes a credible strategy from a generic one
- When to flag that the idea has a fatal flaw

**Result:** Agents produce grammatically correct, strategically empty output. Generic phrases like "Differentiation through unique value proposition and first-mover positioning" pass schema validation but contain zero insight.

**Exception:** Devil's Advocate has actual reasoning instructions — checks reasoning chains, identifies survivorship bias, cites specific numbers. This is the standard every agent should meet.

### Solution

**Replace template-filling prompts with reasoning frameworks per agent.**

Each agent gets a domain-specific reasoning protocol that tells the LLM *how to think*, not just what to output.

**Example — Opportunity Analyst rewrite:**

```python
SYSTEM_PROMPT = """You are a senior venture analyst evaluating a business opportunity.

REASONING PROTOCOL:
1. MARKET TIMING: Why now? What changed in the last 2 years that makes this viable?
   If nothing changed, flag as "timing unclear".
2. DEFENSIBILITY: What stops a well-funded competitor from copying this in 6 months?
   If the answer is "nothing", the competitive strategy must be speed-to-market or network effects, not "differentiation".
3. ICP VALIDATION: Is the ideal customer a real person with budget authority and acute pain?
   If you're guessing, say so. Never fabricate a buyer persona.
4. ASSUMPTION AUDIT: For every number (TAM, price, volume), state the source.
   "Assumed" is valid but confidence MUST be "low".
5. KILL TEST: Is there one thing that, if false, kills this entire opportunity?
   Name it explicitly.

OUTPUT: Return valid JSON with the specified fields. Every field must reflect your reasoning above — not filler text.
"""
```

**Implementation steps:**
1. Write domain-specific reasoning protocols for all 10 agents
2. Each protocol has 4-6 explicit reasoning steps with decision criteria
3. Include "kill conditions" — when should the agent flag a fatal problem instead of filling the template?
4. Add anti-patterns: "Never write [generic phrase]. If you find yourself writing this, you don't have enough information — say so."

**Files to modify:**
- `agents/phase2/opportunity_analyst.py` — lines 16-32 (SYSTEM_PROMPT)
- `agents/phase2/environment_research.py` — SYSTEM_PROMPT
- `agents/phase2/organisation_designer.py` — SYSTEM_PROMPT
- `agents/phase2/swot_synthesizer.py` — SYSTEM_PROMPT
- `agents/phase2/marketing_strategy.py` — SYSTEM_PROMPT
- `agents/phase2/operations.py` — SYSTEM_PROMPT
- `agents/phase2/financial_modelling.py` — SYSTEM_PROMPT
- `agents/phase2/launch_contingency.py` — SYSTEM_PROMPT
- `agents/phase2/summary_agent.py` — SYSTEM_PROMPT

---

## CRITIQUE 2: Multi-Step Reasoning Is Illusory

### Problem

The Intelligence Engine's 4-step chain (DECOMPOSE -> PRODUCE -> CHALLENGE -> REVISE) has no enforcement between steps:

1. **DECOMPOSE** identifies critical judgments but PRODUCE can ignore them entirely
2. **CHALLENGE** finds 5 problems but REVISE gets one attempt with no validation
3. **No iteration** — if REVISE fails to fix what CHALLENGE found, nobody catches it
4. **No constraint propagation** — each step is an independent LLM call that may or may not reference the previous step's output

This is structured prompting (4 sequential LLM calls), not reasoning (enforced logical chain with feedback loops).

### Solution

**Add programmatic enforcement between IE steps.**

```python
async def reason_and_produce(self, ...):
    # Step 1: DECOMPOSE — extract structured judgments
    decomposition = await self._decompose(input_data, cross_context)
    judgments = self._parse_judgments(decomposition)
    # judgments = [{"claim": "...", "evidence_needed": "...", "source": "..."}]

    # Step 2: PRODUCE — generate output with judgment references
    draft = await self._produce(input_data, judgments, schema_prompt)
    
    # ENFORCEMENT: Verify draft addresses all judgments
    coverage = self._check_judgment_coverage(draft, judgments)
    if coverage.missing:
        # Re-run produce with explicit "you missed these" instruction
        draft = await self._produce_with_gaps(draft, coverage.missing)

    # Step 3: CHALLENGE — find problems
    challenges = await self._challenge(draft, decomposition)
    parsed_challenges = self._parse_challenges(challenges)
    # parsed_challenges = [{"type": "math_error", "location": "...", "fix_needed": "..."}]

    # Step 4: REVISE — fix with explicit checklist
    if parsed_challenges:
        revised = await self._revise(draft, parsed_challenges)
        
        # ENFORCEMENT: Verify each challenge was addressed
        unresolved = self._check_challenge_resolution(revised, parsed_challenges)
        if unresolved:
            # Second revision pass — only for unresolved items
            revised = await self._revise_targeted(revised, unresolved)
        
        # ENFORCEMENT: If still unresolved after 2 attempts, downgrade confidence
        still_unresolved = self._check_challenge_resolution(revised, parsed_challenges)
        if still_unresolved:
            revised["confidence_score"] = "low"
            revised["_unresolved_challenges"] = still_unresolved
        
        return revised
    
    return draft
```

**Key changes:**
1. `_parse_judgments()` — extracts structured list from decomposition (not free text)
2. `_check_judgment_coverage()` — programmatically verifies draft addresses each judgment
3. `_parse_challenges()` — extracts structured challenge list (type + location + fix)
4. `_check_challenge_resolution()` — verifies revision actually fixed each challenge
5. Max 2 revision attempts, then forced confidence downgrade

**Files to modify:**
- `agents/phase2/intelligence_engine.py` — lines 113-199 (entire reasoning chain)

---

## CRITIQUE 3: Communication Is Hub-and-Spoke, Not Multi-Agent

### Problem

Every message routes through Mother Agent. Agents cannot:
- Ask each other clarifying questions directly
- Negotiate contradictions without escalating to Alex
- Build on each other's reasoning in real-time

The proposal mechanism exists but is toothless — Marketing *refuses by default* when Financial proposes revenue changes. Mother's resolution is to escalate to Alex rather than force reconciliation.

### Solution

**Implement a Negotiation Protocol with bounded rounds.**

```python
# New file: agents/phase2/negotiation.py

class NegotiationRound:
    """Structured back-and-forth between two agents on a specific claim."""
    
    def __init__(self, initiator: str, responder: str, claim: str, 
                 evidence: dict, max_rounds: int = 3):
        self.initiator = initiator
        self.responder = responder
        self.claim = claim
        self.evidence = evidence
        self.max_rounds = max_rounds
        self.history: list[dict] = []
    
    async def run(self, mother_agent) -> NegotiationResult:
        """Run negotiation. Returns consensus, compromise, or deadlock."""
        
        for round_num in range(self.max_rounds):
            # Initiator states position with evidence
            if round_num == 0:
                position = self._initial_position()
            else:
                position = await self._generate_counter(
                    self.initiator, self.history
                )
            self.history.append({"agent": self.initiator, "position": position})
            
            # Responder evaluates and responds
            response = await self._generate_response(
                self.responder, self.history
            )
            self.history.append({"agent": self.responder, "response": response})
            
            # Check for agreement
            if response["verdict"] == "accept":
                return NegotiationResult(
                    outcome="consensus",
                    agreed_value=response["accepted_value"],
                    rounds=round_num + 1
                )
            elif response["verdict"] == "counter":
                # Continue to next round
                continue
            elif response["verdict"] == "reject_with_evidence":
                # Initiator must evaluate new evidence
                initiator_eval = await self._evaluate_counter_evidence(
                    self.initiator, response["counter_evidence"]
                )
                if initiator_eval["accepts"]:
                    return NegotiationResult(
                        outcome="consensus",
                        agreed_value=initiator_eval["revised_value"],
                        rounds=round_num + 1
                    )
        
        # Deadlock after max_rounds — THEN escalate to Alex
        return NegotiationResult(
            outcome="deadlock",
            initiator_position=self.history[-2],
            responder_position=self.history[-1],
            rounds=self.max_rounds
        )
```

**When to trigger negotiation (in Mother Agent):**
```python
# After backward pass detects contradiction
if contradiction_detected:
    negotiation = NegotiationRound(
        initiator="financial_modelling",
        responder="marketing_strategy",
        claim="Revenue assumptions yield 30-month break-even",
        evidence={"break_even_month": 30, "marketing_year1_revenue": 180000},
        max_rounds=3
    )
    result = await negotiation.run(self)
    
    if result.outcome == "consensus":
        # Update the agreed section with new value
        await self._update_section(result.agreed_value)
    elif result.outcome == "deadlock":
        # NOW escalate to Alex — with both positions summarized
        await self._escalate_with_context(result)
```

**Key principle:** Agents must try to resolve contradictions themselves (3 rounds) before escalating to human. Alex should only see genuinely irreconcilable conflicts.

---

## CRITIQUE 4: Cross-Section Awareness Is Passive

### Problem

Agents receive `cross_section_context` (prior outputs from other sections) but:
- Don't validate consistency with their own output before returning
- Don't reason about conflicts during production
- Just dump context into prompt as background text
- Only Mother checks for contradictions *after* production

### Solution

**Add pre-production consistency check and post-production self-audit to every agent.**

```python
# In base_child_agent.py — new methods

async def _pre_check_consistency(self, input_data: dict, cross_context: dict) -> list[str]:
    """Before producing output, identify constraints from prior sections."""
    if not cross_context:
        return []
    
    constraints = []
    for section, data in cross_context.items():
        relevant = self._extract_relevant_fields(data)
        if relevant:
            constraints.append(
                f"Section {section} established: {json.dumps(relevant)}"
            )
    
    return constraints

async def _post_audit_consistency(self, output: dict, cross_context: dict) -> dict:
    """After producing output, self-check for contradictions."""
    if not cross_context:
        return output
    
    audit_prompt = f"""You just produced this output:
{json.dumps(output, indent=2)[:3000]}

Prior sections established these facts:
{self._format_cross_context(cross_context)[:2000]}

Check for:
1. Numbers that contradict prior sections (e.g., different pricing, different TAM)
2. Assumptions that conflict with established facts
3. Timeline inconsistencies

If you find contradictions, list them. If none, say "CONSISTENT".
"""
    
    audit_result = await self._call_llm(audit_prompt, max_tokens=1024)
    
    if "CONSISTENT" not in audit_result:
        output["_self_audit_warnings"] = audit_result
        # Downgrade confidence if contradictions found
        if output.get("confidence_score") == "high":
            output["confidence_score"] = "medium"
            output["_confidence_reason"] = "Self-audit found potential contradictions"
    
    return output
```

**Modify `handle_request()` flow:**
```python
async def handle_request(self, ...):
    # 1. Pre-check: what constraints exist from prior sections?
    constraints = await self._pre_check_consistency(input_data, cross_context)
    
    # 2. Include constraints in LLM prompt
    prompt = self._build_prompt(validated_input, constraints=constraints)
    
    # 3. Produce output (via IE or direct)
    output = await self._produce(prompt, ...)
    
    # 4. Post-audit: does my output contradict prior sections?
    output = await self._post_audit_consistency(output, cross_context)
    
    # 5. If contradictions found, attempt self-revision before sending
    if output.get("_self_audit_warnings"):
        output = await self._self_revise(output, output["_self_audit_warnings"])
    
    return output
```

**Files to modify:**
- `agents/phase2/base_child_agent.py` — add `_pre_check_consistency()`, `_post_audit_consistency()`
- All child agents — pass constraints to `_build_prompt()`

---

## CRITIQUE 5: Learning Engine Is Logging, Not Learning

### Problem

The Learning Engine:
- Records events (accepted, rejected, edited)
- Injects past failure text into prompts
- Does NOT extract patterns, adjust prompts, or build models of quality

It says "Section was REJECTED: revenue assumptions unrealistic" but doesn't know *why* the assumptions were unrealistic or how to avoid it next time.

### Solution

**Implement pattern extraction + prompt adaptation.**

```python
# Enhanced learning_engine.py

class LearningEngine:
    
    async def extract_failure_pattern(self, session_id: str, section: int, 
                                       rejection_reason: str, original_output: dict) -> dict:
        """Analyze WHY something failed — not just THAT it failed."""
        
        prompt = f"""Analyze this business plan section rejection.

SECTION {section} OUTPUT (rejected):
{json.dumps(original_output, indent=2)[:2000]}

REJECTION REASON: {rejection_reason}

Identify:
1. ROOT CAUSE: What specific reasoning error led to rejection?
   (options: unsupported_claim, math_error, market_ignorance, generic_filler, 
    contradiction, overconfidence, missing_evidence)
2. TRIGGER FIELD: Which specific output field caused the rejection?
3. ANTI-PATTERN: What should the agent NEVER do again in this situation?
4. POSITIVE PATTERN: What should the agent do INSTEAD?

Return JSON: {{"root_cause": "...", "trigger_field": "...", 
               "anti_pattern": "...", "positive_pattern": "..."}}
"""
        
        analysis = await self._call_llm(prompt)
        pattern = json.loads(analysis)
        
        # Store structured pattern (not just event log)
        self._store_pattern(session_id, section, pattern)
        return pattern
    
    def build_learning_context(self, section_number: int) -> str:
        """Build actionable learning context — not just 'this failed before'."""
        patterns = self._get_patterns_for_section(section_number)
        
        if not patterns:
            return ""
        
        lines = ["LEARNED PATTERNS (from past runs):"]
        
        # Group by root cause
        by_cause = defaultdict(list)
        for p in patterns:
            by_cause[p["root_cause"]].append(p)
        
        for cause, instances in by_cause.items():
            count = len(instances)
            latest = instances[-1]
            lines.append(f"\n[{cause.upper()}] (occurred {count}x)")
            lines.append(f"  DO NOT: {latest['anti_pattern']}")
            lines.append(f"  INSTEAD: {latest['positive_pattern']}")
            if latest.get("trigger_field"):
                lines.append(f"  WATCH FIELD: {latest['trigger_field']}")
        
        # Add CEO preference patterns
        edits = self._get_ceo_edit_patterns(section_number)
        if edits:
            lines.append("\nCEO PREFERENCES (from manual edits):")
            for edit in edits[:3]:
                lines.append(f"  - Changed '{edit['field']}': "
                           f"rejected '{edit['old_value'][:50]}', "
                           f"preferred '{edit['new_value'][:50]}'")
        
        return "\n".join(lines)
    
    async def suggest_prompt_adjustment(self, section_number: int) -> Optional[str]:
        """After 3+ failures of same type, suggest SYSTEM_PROMPT change."""
        patterns = self._get_patterns_for_section(section_number)
        
        # Count recurring root causes
        cause_counts = Counter(p["root_cause"] for p in patterns)
        recurring = {k: v for k, v in cause_counts.items() if v >= 3}
        
        if not recurring:
            return None
        
        top_cause = max(recurring, key=recurring.get)
        relevant = [p for p in patterns if p["root_cause"] == top_cause]
        
        return (
            f"Section {section_number} has failed {recurring[top_cause]}x "
            f"due to '{top_cause}'. "
            f"Suggested prompt addition: '{relevant[-1]['positive_pattern']}'"
        )
```

**Files to modify:**
- `agents/phase2/learning_engine.py` — replace simple logging with pattern extraction
- `agents/phase2/mother_agent.py` — call `extract_failure_pattern()` on rejection, not just `record_rejection()`

---

## CRITIQUE 6: Fallback Strategy Is Surrender

### Problem

When IE + direct LLM both fail, agents return hardcoded template defaults:
```python
"competitive_strategy": "Differentiation through unique value proposition..."
```

This is harmful because:
- Downstream agents consume filler as real input
- Financial builds projections on fake assumptions
- The text *looks* real even though it's pre-written boilerplate
- "Low confidence" marker doesn't prevent downstream consumption

### Solution

**Replace template fallback with structured failure modes.**

```python
# In base_child_agent.py

class FailureMode(Enum):
    RETRY_WITH_SIMPLER_PROMPT = "retry_simple"
    PARTIAL_OUTPUT = "partial"
    REFUSE_TASK = "refuse"
    ESCALATE_WITH_CONTEXT = "escalate"

async def _handle_llm_failure(self, input_data: dict, error: str) -> dict:
    """Intelligent failure handling instead of template dump."""
    
    # Strategy 1: Retry with drastically simplified prompt
    simple_prompt = self._build_minimal_prompt(input_data)
    simple_result = await self._call_llm(simple_prompt, max_tokens=512)
    if simple_result and self._is_valid_json(simple_result):
        output = json.loads(simple_result)
        output["_generation_mode"] = "simplified_retry"
        output["confidence_score"] = "low"
        return output
    
    # Strategy 2: Produce partial output (only fields we can derive from inputs)
    partial = self._derive_from_inputs(input_data)
    if partial and len(partial) >= 3:  # At least 3 real fields
        partial["_generation_mode"] = "partial_from_inputs"
        partial["confidence_score"] = "low"
        partial["_missing_fields"] = self._identify_missing(partial)
        return partial
    
    # Strategy 3: Refuse — tell Mother this section cannot be produced
    return {
        "_status": "refused",
        "_reason": f"LLM failed after retry. Error: {error[:200]}",
        "_available_inputs": list(input_data.keys()),
        "confidence_score": "none",
        "section_number": str(self.SECTION_NUMBER),
    }

def _derive_from_inputs(self, input_data: dict) -> dict:
    """Extract what we can directly from inputs without LLM."""
    # Each agent overrides this with domain logic
    # e.g., Financial can compute break-even from revenue/cost inputs
    # Marketing can extract competitor list from environment research
    return {}
```

**Mother Agent handling refused tasks:**
```python
async def _handle_refused_task(self, task_id, section, refusal):
    """When a child refuses, Mother decides: retry, skip, or block."""
    reason = refusal.get("_reason", "")
    
    if "timeout" in reason.lower():
        # Bedrock timeout — retry with longer timeout
        await self._retry_task(task_id, timeout_override=180)
    elif "parse" in reason.lower():
        # Parse failure — retry with Sonnet instead of Haiku
        await self._retry_task(task_id, model_override="sonnet")
    else:
        # Genuine failure — mark section as blocked, notify Alex
        self._mark_section_blocked(section, reason)
        await self._notify_alex(f"Section {section} could not be produced: {reason}")
```

**Key principle:** Never silently produce garbage. Either produce real output, produce partial output with clear markers, or refuse and let Mother decide.

**Files to modify:**
- `agents/phase2/base_child_agent.py` — replace `_fallback_defaults()` with `_handle_llm_failure()`
- All child agents — implement `_derive_from_inputs()` with domain logic
- `agents/phase2/mother_agent.py` — add `_handle_refused_task()`

---

## CRITIQUE 7: SPADE/XMPP Is Massive Overhead for Zero Benefit

### Problem

The system runs a Prosody XMPP server with 12 agent JIDs, CyclicBehaviours, and presence signals — for agents that execute **in the same Python process on the same machine**.

Costs:
- 30s startup overhead for XMPP connections
- `time.sleep(1)` blocking the event loop while polling readiness
- XMPP server is a single point of failure
- Connection flakiness adds non-determinism
- Debugging message routing requires XMPP knowledge

Benefits: None. No agent runs on a different machine. No horizontal scaling. No real distribution.

### Solution

**Replace SPADE with direct async calls. Keep ACL format for audit logging.**

```python
# New file: agents/phase2/message_bus.py

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ACLMessage:
    """ACL message format preserved for audit trail — no XMPP transport."""
    sender: str
    receiver: str
    performative: str  # request, inform, escalate, propose, refuse, revise
    content: dict
    task_id: str = ""
    session_id: str = ""
    pipeline_run_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MessageBus:
    """In-process message bus replacing SPADE/XMPP.
    
    - Direct async dispatch (no network hop)
    - Full audit logging (same as before)
    - Agent registration by name (no JIDs)
    """
    
    def __init__(self, supabase_client=None):
        self._handlers: dict[str, Callable] = {}
        self._supabase = supabase_client
        self._message_log: list[ACLMessage] = []
    
    def register(self, agent_name: str, handler: Callable):
        """Register an agent's message handler."""
        self._handlers[agent_name] = handler
    
    async def send(self, message: ACLMessage):
        """Dispatch message directly to target agent's handler."""
        # Log for audit
        self._message_log.append(message)
        if self._supabase:
            await self._persist_message(message)
        
        handler = self._handlers.get(message.receiver)
        if not handler:
            logger.error("No handler registered for agent: %s", message.receiver)
            return
        
        logger.info(
            "[MessageBus] %s -> %s (%s) task=%s",
            message.sender, message.receiver, 
            message.performative, message.task_id
        )
        
        # Direct async call — no network, no serialization overhead
        await handler(message)
    
    async def broadcast(self, sender: str, agent_names: list[str], 
                        performative: str, content: dict, **kwargs):
        """Send same message to multiple agents in parallel."""
        messages = [
            ACLMessage(
                sender=sender, receiver=name,
                performative=performative, content=content, **kwargs
            )
            for name in agent_names
        ]
        await asyncio.gather(*[self.send(msg) for msg in messages])
    
    async def _persist_message(self, message: ACLMessage):
        """Write to Supabase agent_messages table (same schema as before)."""
        self._supabase.table("agent_messages").insert({
            "sender": message.sender,
            "receiver": message.receiver,
            "performative": message.performative,
            "content": json.dumps(message.content),
            "task_id": message.task_id,
            "session_id": message.session_id,
            "pipeline_run_id": message.pipeline_run_id,
            "timestamp": message.timestamp,
        }).execute()
```

**Migration path:**
1. Build `MessageBus` alongside SPADE (dual-write for 1 week)
2. Replace `send_acl()` calls with `bus.send()` calls
3. Remove SPADE `setup()`, `start()`, JID configuration
4. Remove Prosody server dependency
5. Remove `time.sleep(1)` readiness polling (agents register on import)

**Estimated impact:**
- Startup: 30s -> <1s
- Reliability: Remove XMPP connection failures
- Debugging: Direct Python call stack instead of XMPP message trace
- Lines removed: ~200 (SPADE boilerplate across all agents)

**Files to modify:**
- New: `agents/phase2/message_bus.py`
- `agents/phase2/mother_agent.py` — replace `send_acl()` and SPADE behaviours
- `agents/phase2/base_child_agent.py` — replace `ListenBehaviour` with handler registration
- `agents/phase2/council_agent.py` — same
- `agents/phase2/devils_advocate.py` — same
- Remove: `config/phase2/prosody.cfg.lua`

---

## CRITIQUE 8: Agents Have No Beliefs or Autonomy

### Problem

Child agents are stateless — they receive input, call LLM, return output. They have:
- No persistent beliefs about the business
- No ability to disagree with Mother's framing
- No initiative to request missing information
- No memory of what they previously produced in this session

They are functions, not agents. The "multi-agent" label is architectural, not behavioral.

### Solution

**Implement a lightweight Belief-Desire-Intention (BDI) layer per agent.**

```python
# New file: agents/phase2/agent_beliefs.py

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Belief:
    """A fact this agent considers true, with provenance."""
    claim: str
    confidence: float  # 0.0 - 1.0
    source: str  # "own_analysis", "section_3", "ceo_input", "market_data"
    established_at: str  # ISO timestamp
    challenged_by: list[str] = field(default_factory=list)


class AgentBeliefStore:
    """Per-agent belief system. Persists across tasks within a session."""
    
    def __init__(self, agent_name: str, redis_client):
        self.agent_name = agent_name
        self.redis = redis_client
        self._beliefs: dict[str, Belief] = {}
    
    def assert_belief(self, key: str, claim: str, confidence: float, source: str):
        """Agent asserts something it believes to be true."""
        self._beliefs[key] = Belief(
            claim=claim, confidence=confidence, source=source,
            established_at=datetime.now(timezone.utc).isoformat()
        )
        self._persist()
    
    def challenge_belief(self, key: str, challenger: str, counter_evidence: str) -> bool:
        """Another agent challenges this belief. Returns True if belief changed."""
        belief = self._beliefs.get(key)
        if not belief:
            return False
        
        belief.challenged_by.append(f"{challenger}: {counter_evidence}")
        
        # If challenged by 2+ agents or by a more authoritative source, reduce confidence
        if len(belief.challenged_by) >= 2:
            belief.confidence *= 0.5
            self._persist()
            return True
        
        return False
    
    def get_beliefs_for_prompt(self) -> str:
        """Format beliefs for inclusion in LLM prompt."""
        if not self._beliefs:
            return ""
        
        lines = ["MY CURRENT BELIEFS (from prior analysis in this session):"]
        for key, belief in self._beliefs.items():
            status = ""
            if belief.challenged_by:
                status = f" [CHALLENGED by {len(belief.challenged_by)} agents]"
            lines.append(
                f"- {belief.claim} (confidence: {belief.confidence:.1f}, "
                f"source: {belief.source}){status}"
            )
        return "\n".join(lines)
    
    def get_conflicts_with(self, incoming_data: dict) -> list[dict]:
        """Detect conflicts between beliefs and new incoming data."""
        conflicts = []
        for key, belief in self._beliefs.items():
            if key in incoming_data:
                incoming_value = incoming_data[key]
                if self._is_contradictory(belief.claim, incoming_value):
                    conflicts.append({
                        "belief_key": key,
                        "my_belief": belief.claim,
                        "incoming": incoming_value,
                        "incoming_confidence": incoming_data.get(
                            f"{key}_confidence", "unknown"
                        ),
                    })
        return conflicts
```

**Integration with agents:**
```python
# In base_child_agent.py

async def handle_request(self, ...):
    # Check if incoming data conflicts with my beliefs
    conflicts = self.beliefs.get_conflicts_with(cross_section_context)
    
    if conflicts:
        # I disagree with something upstream said
        for conflict in conflicts:
            if conflict["incoming_confidence"] == "high" and \
               self.beliefs._beliefs[conflict["belief_key"]].confidence < 0.7:
                # Incoming is more confident — update my belief
                self.beliefs.assert_belief(
                    conflict["belief_key"],
                    conflict["incoming"],
                    confidence=0.8,
                    source=f"updated_from_{conflict.get('source', 'upstream')}"
                )
            else:
                # I'm more confident — propose revision to upstream
                await self._propose_revision(conflict)
    
    # Include my beliefs in prompt
    belief_context = self.beliefs.get_beliefs_for_prompt()
    prompt = self._build_prompt(validated_input, beliefs=belief_context)
    
    # After producing output, update beliefs
    output = await self._produce(prompt)
    self._update_beliefs_from_output(output)
    
    return output
```

**Files to create:**
- `agents/phase2/agent_beliefs.py`

**Files to modify:**
- `agents/phase2/base_child_agent.py` — integrate belief store into `handle_request()`

---

## CRITIQUE 9: Mother Agent Is a God Object (2500 Lines)

### Problem

Mother Agent contains ALL intelligence: orchestration, quality gates, coherence audit, backward pass, confidence ceilings, constitution enforcement, evidence grading, task retry, delivery, Web Interface notifications.

2509 lines, 69 methods, untestable, impossible to modify safely.

### Solution

**Split into focused, composable classes.**

```
mother_agent.py (slim orchestrator — ~300 lines)
├── pipeline_orchestrator.py  — execution groups, dependency resolution
├── quality_gate.py           — DA routing, council routing, so-what filter
├── coherence_auditor.py      — cross-section consistency checks
├── conflict_resolver.py      — backward pass, negotiation, escalation
├── delivery_manager.py       — document compilation, Web Interface, Supabase writes
└── task_retry_manager.py     — retry logic, circuit breaker, fallback decisions
```

```python
# agents/phase2/mother_agent.py (after split)

class MotherAgent:
    """Slim orchestrator. Delegates to focused subsystems."""
    
    def __init__(self, config, message_bus, redis, supabase):
        self.bus = message_bus
        self.pipeline = PipelineOrchestrator(config, message_bus)
        self.quality = QualityGate(config, message_bus)
        self.coherence = CoherenceAuditor(config)
        self.conflict = ConflictResolver(config, message_bus)
        self.delivery = DeliveryManager(config, supabase)
        self.retry = TaskRetryManager(config, redis)
    
    async def run_pipeline(self, session_id: str, idea: str, phase1_data: dict):
        """Main pipeline — delegates to subsystems."""
        run_id = str(uuid4())
        
        for group in self.pipeline.execution_order():
            tasks = self.pipeline.generate_tasks(group, self._prior_outputs)
            results = await self.pipeline.execute_group(tasks)
            
            # Quality gate
            for task_id, output in results.items():
                output = await self.quality.run_gates(task_id, output)
                
                if output.get("_status") == "refused":
                    output = await self.retry.handle_failure(task_id, output)
            
            # Coherence check after each group
            audit = await self.coherence.audit(self._prior_outputs)
            if audit.contradictions:
                await self.conflict.resolve(audit.contradictions)
        
        # Final delivery
        await self.delivery.compile_and_send(session_id, run_id, self._prior_outputs)
```

**Files to create:**
- `agents/phase2/pipeline_orchestrator.py`
- `agents/phase2/quality_gate.py`
- `agents/phase2/coherence_auditor.py`
- `agents/phase2/conflict_resolver.py`
- `agents/phase2/delivery_manager.py`
- `agents/phase2/task_retry_manager.py`

**Files to modify:**
- `agents/phase2/mother_agent.py` — reduce from 2500 to ~300 lines

---

## CRITIQUE 10: No Adaptive Pipeline (Blind Execution)

### Problem

The pipeline executes all sections regardless of early findings. If Section 3 (Environment Research) discovers the market is saturated — all Five Forces at "high threat" — the pipeline still runs 8 more sections, building a full business plan for a doomed idea.

This wastes:
- 8+ LLM calls (at $0.01-0.03 each)
- 3-5 minutes of runtime
- Alex's time reading a plan that shouldn't exist

### Solution

**Implement early-kill checkpoints after critical sections.**

```python
# In pipeline_orchestrator.py

KILL_CHECKPOINTS = {
    "1": {  # After Opportunity Analyst
        "kill_condition": lambda output: (
            output.get("confidence_score") == "low" and
            "no clear differentiation" in output.get("competitive_strategy", "").lower()
        ),
        "message": "Opportunity analysis shows no differentiation. Continue anyway?"
    },
    "3": {  # After Environment Research
        "kill_condition": lambda output: (
            sum(1 for force in output.get("five_forces", {}).values()
                if force.get("threat_level") == "high") >= 4
        ),
        "message": "4/5 competitive forces are high threat. Market may be unwinnable."
    },
    "12": {  # After Financial Modelling
        "kill_condition": lambda output: (
            output.get("break_even_analysis", {}).get("baseline_month", 0) > 48
        ),
        "message": "Break-even exceeds 48 months. Business model may not be viable."
    },
}

async def _check_kill_condition(self, section: str, output: dict) -> bool:
    """Check if pipeline should pause for CEO decision."""
    checkpoint = KILL_CHECKPOINTS.get(section)
    if not checkpoint:
        return False
    
    if checkpoint["kill_condition"](output):
        # Ask Alex: continue, pivot, or kill?
        response = await self._ask_alex_checkpoint(
            section, checkpoint["message"], output
        )
        
        if response == "kill":
            self._archive_pipeline(reason=checkpoint["message"])
            return True  # Stop pipeline
        elif response == "pivot":
            # Alex provides pivot direction — restart from section 1
            pivot_input = await self._get_pivot_input()
            await self._restart_pipeline(pivot_input)
            return True
        # "continue" — proceed normally
    
    return False
```

**Files to modify:**
- `agents/phase2/pipeline_orchestrator.py` (or `mother_agent.py`) — add checkpoint logic after each group completes

---

## SUMMARY: Priority Order for Implementation

| Priority | Critique | Impact | Effort |
|----------|----------|--------|--------|
| 1 | Kill SPADE, use MessageBus | Reliability + 30s startup savings | Medium |
| 2 | Split Mother Agent | Testability, maintainability | High |
| 3 | Reasoning-aware prompts | Output quality (biggest user-visible impact) | Medium |
| 4 | Enforce IE reasoning chain | Output quality | Medium |
| 5 | Negotiation protocol | True MAS behavior | Medium |
| 6 | Replace fallback with failure modes | Prevent garbage propagation | Low |
| 7 | Active cross-section awareness | Consistency | Medium |
| 8 | Real learning engine | Long-term improvement | High |
| 9 | Agent beliefs (BDI) | Autonomy | High |
| 10 | Adaptive pipeline / early kill | Cost savings, user experience | Low |

**Recommended execution order:** 1 → 6 → 3 → 4 → 2 → 10 → 5 → 7 → 8 → 9

Start with low-effort, high-impact fixes (MessageBus, failure modes, prompts), then tackle structural changes (Mother split), then build true intelligence (negotiation, beliefs, learning).
