# Missing Implementations — Business Plan Sections

## Status as of 2026-06-11

### ✅ IMPLEMENTED (10 core agents + infrastructure)
- **Section 1**: Opportunity Analyst ✅
- **Section 3**: Environment Research ✅
- **Section 4**: Organisation Designer ✅
- **Section 5**: SWOT Synthesizer ✅
- **Section 8**: Marketing Strategy ✅ (includes LTV, CAC, unit economics)
- **Section 10**: Operations ✅
- **Section 12**: Financial Modelling ✅ (includes SimPy simulation)
- **Section 13**: Launch & Contingency (Start-up programme) ✅
- **Tech Stack & Data Privacy**: Tech Stack Agent ✅ (added June 4)
- **Executive Summary**: Summary Agent ✅
- **Quality Gates**: Devil's Advocate ✅, Council Agent ✅
- **Infrastructure**: Intelligence Engine, Learning Engine, Document Compiler, Coherence Auditor

---

## 🔴 MISSING AGENTS (6 sections defined in dependency_map.yaml)

### 1. **Section 2: Entrepreneur and Development Team**
**Status**: ❌ Schema missing, Agent missing
**Defined in**: `dependency_map.yaml` lines 26-42
**Purpose**: Founder profiles, team composition, credibility assessment
**Dependencies**: Section 1 (Opportunity)
**Condition**: `always_required: false` — include when team not yet established or investor audience requires profiles
**Input fields**: `founder_profile`, `team_composition` (from CEO answers)
**Output fields**: `team_profiles`, `team_credibility_score`

**Why it matters**: Investors assess team first. Without this section, business plans look like idea-only pitches.

**Implementation priority**: HIGH (P0) — standard business plan requirement

---

### 2. **Section 6: R&D and Technology**
**Status**: ❌ Schema missing, Agent missing
**Defined in**: `dependency_map.yaml` lines 108-125
**Purpose**: Technology innovation, IP analysis, development roadmap
**Dependencies**: Section 1 (Opportunity)
**Condition**: `always_required: false` — include only when business is built on technology innovation or patent
**Input fields**: `technology_description`, `ip_status` (from CEO answers)
**Output fields**: `rd_plan`, `ip_analysis`

**Why it matters**: For deep-tech or patent-driven businesses, this is core defensibility.

**Implementation priority**: MEDIUM (P1) — conditional section, only needed for tech-heavy businesses

---

### 3. **Section 7: Agreements, Alliances, and Outsourcing**
**Status**: ❌ Schema missing, Agent missing
**Defined in**: `dependency_map.yaml` lines 127-142
**Purpose**: Partnership strategy, outsourcing decisions
**Dependencies**: Section 1 (Opportunity), Section 5 (SWOT)
**Condition**: `always_required: false` — include when partnerships or outsourcing are core to business model
**Input fields**: `partnership_targets`, `competitive_strategy` (from Section 1)
**Output fields**: `alliance_plan`, `outsourcing_strategy`

**Why it matters**: Marketplace, platform, or partnership-heavy business models need this explicitly.

**Implementation priority**: MEDIUM (P1) — conditional section

---

### 4. **Section 9: Quality Management**
**Status**: ❌ Schema missing, Agent missing
**Defined in**: `dependency_map.yaml` lines 173-187
**Purpose**: Quality assurance approach, delivery consistency
**Dependencies**: Section 1 (Opportunity), Section 8 (Marketing)
**Condition**: `always_required: false` — include for service businesses where delivery consistency is key differentiator
**Input fields**: `service_description` (from prior tasks)
**Output fields**: `quality_policy`, `quality_procedures`

**Why it matters**: For B2B SaaS, consulting, or regulated industries (healthcare, finance), quality processes are material.

**Implementation priority**: LOW (P2) — narrow use case

---

### 5. **Section 11: Human Resources Plan**
**Status**: ⚠️ Schema missing, partially handled by Organisation Designer
**Defined in**: `dependency_map.yaml` lines 210-233
**Purpose**: Roles, hiring plan, compensation, knowledge gaps
**Dependencies**: Section 1 (Opportunity), Section 5 (SWOT)
**Condition**: `always_required: true` — this is a CORE section
**Input fields**: `capability_gaps` (from Section 4), `business_model` (from Section 1), `strategic_implications` (from SWOT)
**Output fields**: `roles_and_responsibilities`, `personnel_policy`, `headcount_plan`, `knowledge_gaps`

**Current workaround**: Organisation Designer (Section 4) outputs `headcount_plan`, but it doesn't cover personnel policy, hiring timeline, or compensation strategy.

**Why it matters**: Financial model (Section 12) depends on `headcount_plan` from Section 11. Marketing depends on sales team sizing. Launch plan depends on hiring milestones.

**Implementation priority**: HIGH (P0) — this is a required section and is referenced by Sections 12, 13

**Recommendation**: Either:
- **Option A**: Extend Organisation Designer to output full HR plan (roles, hiring timeline, comp strategy)
- **Option B**: Create standalone `hr_plan_agent.py` that runs after SWOT (Section 5)

---

### 6. **Section 14: Contingency Plan & Exit Strategy**
**Status**: ⚠️ **Schemas exist**, Agent missing
**Defined in**: `dependency_map.yaml` lines 290-306
**Schemas**: `schemas/inputs/exit_strategy.py`, `schemas/outputs/exit_strategy.py` ✅
**Purpose**: Failure scenarios, exit conditions, wind-down plan
**Dependencies**: Section 12 (Financial), Section 13 (Launch)
**Condition**: `always_required: false` — include when risk profile is high
**Input fields**: `risk_factors` (from Section 12), `probability_distribution` (from SimPy)
**Output fields**: `contingency_scenarios`, `exit_conditions`

**Current state**: Schemas created June 11 (commit 2567449) but agent not built yet.

**Exit Strategy schema** (also in `exit_strategy.py`) covers:
- Primary exit path (acquisition, IPO)
- Cap table evolution (pre-seed → seed → Series A → exit)
- Funding strategy (round sizes, timing, milestones)
- Investor returns (multiples, dilution analysis)

**Why it matters**: VC-backed businesses must show exit path. Bootstrap businesses need contingency/pivot triggers.

**Implementation priority**: MEDIUM (P1) — important for investor-focused business plans, less critical for self-funded

---

## 📊 SUMMARY TABLE

| Section | Name | Status | Priority | Reason |
|---------|------|--------|----------|--------|
| 2 | Entrepreneur & Team | ❌ Missing | **P0 HIGH** | Investors assess team first |
| 6 | R&D & Technology | ❌ Missing | P1 MEDIUM | Conditional — tech/IP businesses only |
| 7 | Alliances & Outsourcing | ❌ Missing | P1 MEDIUM | Conditional — partnership-heavy models |
| 9 | Quality Management | ❌ Missing | P2 LOW | Conditional — service businesses only |
| 11 | Human Resources | ⚠️ Partial | **P0 HIGH** | Required — Financial depends on this |
| 14 | Contingency & Exit | ⚠️ Schema only | P1 MEDIUM | Important for investor plans |
| Tech Stack | Infrastructure & Compliance | ✅ Done | — | Added June 4, 2026 |

---

## 🎯 IMPLEMENTATION ROADMAP

### Phase 1 (P0 — Core Sections, 2-3 days)

#### 1. Section 2: Entrepreneur & Team Agent
**Files to create**:
- `agents/phase2/entrepreneur_team.py`
- `schemas/inputs/entrepreneur_team.py`
- `schemas/outputs/entrepreneur_team.py`

**Schema structure** (input):
```python
class EntrepreneurTeamInput(BaseModel):
    task_id: str
    session_id: str
    opportunity_description: str  # from Section 1
    competitive_strategy: str  # from Section 1
    founder_profile: Optional[str] = None  # CEO answer
    team_composition: Optional[dict] = None  # CEO answer
    acceptance_criteria: str = ""
```

**Schema structure** (output):
```python
class EntrepreneurTeamOutput(BaseModel):
    section_number: str = "2"
    founder_profiles: List[dict]  # name, role, background, relevant_experience, credibility_score
    team_strengths: List[str]  # what the team is good at
    team_gaps: List[str]  # capabilities the team lacks (feeds Section 11 HR plan)
    team_credibility_assessment: str  # overall assessment of team fit
    assumptions_used: List[Assumption]
    uncertainties: List[str]
    confidence_score: Literal["high", "medium", "low"]
```

**Reasoning framework** (for SYSTEM_PROMPT):
- **Founder-Market Fit**: Does the founder's background match the problem domain?
- **Complementary Skills**: Does the team cover product, sales, ops?
- **Execution Track Record**: Have they launched anything before?
- **Gap Analysis**: What roles are missing and how critical are they?
- **Red Flags**: Solo founder in complex market? All-technical team with no GTM experience?

**Agent behavior**:
- If `founder_profile` and `team_composition` are empty, escalate with gap_key `founder_profile`
- Run Intelligence Engine 2-step (decompose + produce) — this section is ungated
- Send `inform` to Mother (not Council-gated)

---

#### 2. Section 11: HR Plan Agent (or extend Organisation Designer)

**Option A: Create new agent** `agents/phase2/hr_plan.py`

**Schema structure** (input):
```python
class HRPlanInput(BaseModel):
    task_id: str
    session_id: str
    capability_gaps: List[dict]  # from Section 4 org designer
    business_model: str  # from Section 1
    strategic_implications: str  # from Section 5 SWOT
    revenue_assumptions: dict  # from Section 8 (to size sales team)
    acceptance_criteria: str = ""
```

**Schema structure** (output):
```python
class HRPlanOutput(BaseModel):
    section_number: str = "11"
    roles_and_responsibilities: List[dict]  # role_title, responsibilities, required_by_month, cost_range
    hiring_timeline: List[dict]  # month, role, justification
    headcount_plan: dict  # {month: headcount, month: total_cost} — feeds Section 12
    personnel_policy: str  # compensation approach, equity policy, contractor vs FTE
    knowledge_gaps: List[str]  # skills the business must acquire (training, advisory, hiring)
    assumptions_used: List[Assumption]
    uncertainties: List[str]
    confidence_score: Literal["high", "medium", "low"]
```

**Reasoning framework**:
- **Hiring Sequencing**: Who is needed *when*? First hire: sales vs engineer vs ops?
- **Cost Realism**: Are salary estimates market-rate for the geography?
- **Capacity Match**: Does sales headcount support revenue targets from Section 8?
- **Capability Closure**: Does the hiring plan close the `capability_gaps` from Section 4?

**Dependency changes**:
- Section 12 (Financial) currently depends on `headcount_plan` from Section 11
- **Currently broken**: Financial gets `headcount_plan` from Section 4, not Section 11
- **Fix**: Update `dependency_map.yaml` Section 12 inputs to pull `headcount_plan` from Section 11 instead of Section 4

---

### Phase 2 (P1 — Investor Sections, 3-4 days)

#### 3. Section 14: Exit Strategy & Contingency Agent

**Files to create**:
- `agents/phase2/exit_strategy.py` (schemas already exist ✅)

**Agent logic**:
- Reads `risk_factors` from Section 12 (Financial)
- Reads `probability_distribution` from Section 12 (SimPy P10/P50/P90 outcomes)
- Determines primary exit path based on business type:
  - **SaaS → Acquisition** (typical 3-7 year timeline, 5-10x revenue multiples)
  - **Marketplace → IPO or Strategic** (needs scale, network effects)
  - **Deep Tech → Acquisition** (by incumbent with distribution)
- Builds cap table evolution: pre-seed (founders 100%) → seed (85/15) → Series A (65/35) → exit (founders 40-50%)
- Calculates investor returns: seed investors at 10x, Series A at 5x
- Designs contingency triggers: "If revenue < $X by Month Y, pivot or wind down"

**Reasoning framework**:
- **Exit Realism**: Don't claim IPO for a $5M ARR SaaS business — IPO threshold is $100M+ ARR
- **Acquirer Logic**: Name 3-5 plausible acquirers and why they'd buy (strategic fit, talent, IP, distribution)
- **Dilution Math**: Founder ownership must be realistic. If you raise $5M at $20M post, founders dilute 20%, not 10%
- **Contingency Triggers**: Must be observable and actionable — "pivot if CAC > $500 after 6 months" is good, "pivot if market conditions worsen" is not

**Model**: Sonnet (exit strategy requires strategic reasoning)
**Council-gated**: No (but could be — exit strategy is high-stakes for investors)

---

#### 4. Section 6: R&D and Technology Agent

**Only needed for**: Deep tech, biotech, hardware, patent-driven businesses

**Schema structure** (input):
```python
class RDTechnologyInput(BaseModel):
    task_id: str
    session_id: str
    technology_description: str  # CEO answer
    ip_status: Optional[str] = None  # patent filed, provisional, trade secret, none
    competitive_strategy: str  # from Section 1
    acceptance_criteria: str = ""
```

**Schema structure** (output):
```python
class RDTechnologyOutput(BaseModel):
    section_number: str = "6"
    rd_plan: dict  # stages, milestones, timeline_to_market, cost_estimate
    ip_analysis: dict  # patent_status, defensibility_score, freedom_to_operate
    technology_risk: str  # what could go wrong technically
    assumptions_used: List[Assumption]
    uncertainties: List[str]
    confidence_score: Literal["high", "medium", "low"]
```

**Reasoning framework**:
- **IP Strength**: Patent filed = strong, trade secret = medium, "first to market" = weak
- **Development Risk**: TRL (Technology Readiness Level) 1-3 = high risk, 7-9 = ready for commercialization
- **Cost Realism**: R&D burn rate must match Financial model (Section 12)

**Model**: Haiku (narrow technical assessment)
**Council-gated**: No

---

### Phase 3 (P2 — Niche Sections, 2-3 days)

#### 5. Section 7: Alliances & Outsourcing Agent
#### 6. Section 9: Quality Management Agent

These are conditional sections for specific business types. Lower priority.

---

## 🔧 INTEGRATION CHANGES NEEDED

### 1. Update `config/phase2/agent_roster.yaml`
Add entries for:
```yaml
entrepreneur_team:
  jid_env: "ENTREPRENEUR_TEAM_JID"
  sections_owned: ["2"]
  model: "claude-haiku"
  timeout_seconds: 90

hr_plan:
  jid_env: "HR_PLAN_JID"
  sections_owned: ["11"]
  model: "claude-haiku"
  timeout_seconds: 90

exit_strategy:
  jid_env: "EXIT_STRATEGY_JID"
  sections_owned: ["14"]
  model: "claude-sonnet"
  timeout_seconds: 120
```

### 2. Update `config/phase2/dependency_map.yaml`
Ensure Section 12 depends on Section 11 (not Section 4) for `headcount_plan`.

### 3. Update `main.py` to spawn new agents

### 4. Update `.env` with new JIDs and passwords

---

## 📝 NOTES

- **Tech Stack Agent** already exists (added June 4) — covers infrastructure, data privacy, GDPR/CCPA compliance
- **Unit Economics** (LTV, CAC, LTV:CAC ratio, payback period) added to Marketing Agent (Section 8) on June 4
- **Exit Strategy schemas** created June 11 but agent not built yet
- **Section 14** in `dependency_map.yaml` is called "Contingency plan" but the schema is called "Exit Strategy" — they should be merged into one agent that covers both contingency (failure scenarios) and exit strategy (acquisition/IPO path)

---

## ✅ CHECKLIST FOR COMPLETION

- [ ] Section 2: Entrepreneur & Team Agent
- [ ] Section 11: HR Plan Agent (or extend Organisation Designer)
- [ ] Section 14: Exit Strategy & Contingency Agent
- [ ] Section 6: R&D & Technology Agent (conditional)
- [ ] Section 7: Alliances & Outsourcing Agent (conditional)
- [ ] Section 9: Quality Management Agent (conditional)
- [ ] Update agent_roster.yaml with new agents
- [ ] Update dependency_map.yaml to fix Section 12 → Section 11 dependency
- [ ] Add new agents to Mother Agent startup sequence
- [ ] Test full pipeline with all sections enabled
