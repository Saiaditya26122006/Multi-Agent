# 🤖 Multi-Agent AI System — Complete Explanation

## 📖 Table of Contents
1. [What is this project?](#what-is-this-project)
2. [Why was it built?](#why-was-it-built)
3. [System Architecture Overview](#system-architecture-overview)
4. [Phase 1: Foundation Pipeline](#phase-1-foundation-pipeline)
5. [Phase 2: Multi-Agent Business Plan Generation](#phase-2-multi-agent-business-plan-generation)
6. [Evaluation Pipeline](#evaluation-pipeline)
7. [Technology Stack Deep Dive](#technology-stack-deep-dive)
8. [Data Flow: End-to-End Journey](#data-flow-end-to-end-journey)
9. [All Agents Explained](#all-agents-explained)
10. [Why This Approach Works](#why-this-approach-works)

---

## 🎯 What is this project?

**EpistemicOS** is an AI-powered business plan generation system that works like a **virtual consulting team**. 

Instead of one AI doing everything, this system uses **multiple specialized AI agents** that:
- Talk to the CEO (via Telegram)
- Ask clarifying questions
- Research the business idea
- Generate different sections of a business plan (market analysis, financials, marketing, etc.)
- Verify their own work
- Flag uncertainties and gaps in data

Think of it as hiring 10+ expert consultants who:
- Each specialize in one area (finance, marketing, operations, etc.)
- Work together by passing information between them
- Double-check each other's work
- Tell you explicitly what they DON'T know (instead of making things up)

---

## 💡 Why was it built?

### The Problem
Traditional AI chatbots (like ChatGPT) have major issues when building business plans:
1. **Hallucination**: They make up fake data and present it as fact
2. **No memory**: They forget context from earlier in the conversation
3. **No collaboration**: One AI tries to do everything, leading to shallow analysis
4. **No verification**: No system to check if the output is actually correct

### The Solution
This system solves these problems by:
1. **Multi-agent approach**: Specialized agents for each task (like a real consulting team)
2. **Explicit uncertainty tracking**: Every agent flags what it doesn't know
3. **Grounding with real data**: Uses web search, CEO data, and databases to verify facts
4. **Memory system**: Redis + Supabase stores all context permanently
5. **Evaluation layer**: Separate agents verify the business plan's quality

### Real-world use case
A CEO (named Alex) wants to validate a business idea. Instead of:
- Hiring expensive consultants (€10K-50K)
- Spending weeks on research
- Getting a plan with unverified assumptions

They can:
- Send the idea via Telegram
- Answer a few clarifying questions
- Get a full business plan in 30 minutes
- See exactly what data is missing and where to find it

---

## 🏗️ System Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM (CEO Interface)                  │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      PHASE 1 PIPELINE                        │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │ L0 Guard │→→→│ L1 Clarity│→→→│ L2 Research│→→│L3 Feedback││
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
│                                                               │
│  Purpose: Extract idea, ask questions, gather context        │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                   EVALUATION PIPELINE                        │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  • Web search (Tavily)                                │   │
│  │  • Knowledge base retrieval                           │   │
│  │  • Data declaration (what data is needed)             │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      PHASE 2 PIPELINE                        │
│                      (Mother Agent + 9 Child Agents)         │
│                                                               │
│           ┌─────────────────────────────────┐                │
│           │       MOTHER AGENT              │                │
│           │  (Orchestrates everything)      │                │
│           └────────┬────────────────────────┘                │
│                    │                                          │
│     ┌──────────────┼──────────────┐                          │
│     ▼              ▼              ▼                          │
│  Agent 1       Agent 2  ...   Agent 9                        │
│  (Opportunity) (Environment)  (Summary)                      │
│                                                               │
│  Each agent generates one section of the business plan       │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      OUTPUT DELIVERY                         │
│  • Text summary via Telegram                                 │
│  • Professional DOCX with styling                            │
│  • Confidence scores + gap analysis                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔷 Phase 1: Foundation Pipeline

Phase 1 is the **onboarding and context-gathering** pipeline. It runs BEFORE the business plan generation.

### Purpose
Extract the business idea, clarify vague inputs, and gather CEO context (background, goals, constraints).

### Agents in Phase 1

#### **L0: Input Guard** (`l0_input_guard.py`)
**Role**: Security checkpoint — validates every incoming message

**What it does**:
1. Checks if the sender is authorized (is this the CEO?)
2. Checks for duplicate messages (prevents processing the same message twice)
3. Creates or retrieves the session (every conversation is a session)
4. Logs the message to Supabase
5. Passes valid messages to L1

**Why it exists**:
- Prevents spam and unauthorized access
- Ensures every message is logged for audit trail
- Manages session lifecycle

**Example flow**:
```
Telegram message arrives → L0 checks sender → Creates session UUID → 
Logs to Supabase → Passes to L1
```

---

#### **L1: Clarity Agent** (`l1_clarity_agent.py`)
**Role**: Question asker — extracts information from vague CEO messages

**What it does**:
1. Reads CEO's message (e.g., "I want to build a SaaS for academic publishing")
2. Checks what information is missing:
   - Target customer?
   - Revenue model?
   - Key constraints (budget, timeline)?
3. Generates ONE focused question at a time (max 3 questions per session)
4. Stores the Q&A as "assumptions" in Supabase
5. Stops asking when enough context is gathered

**Why it exists**:
- Most CEOs don't provide full details upfront
- Focused questions → better quality answers
- One question at a time → less overwhelming for the CEO

**Example flow**:
```
CEO: "I want to build an AI tool for researchers"
L1: "What specific problem do researchers face that your tool solves?"
CEO: "Manuscript rejection due to formatting errors"
L1: "Who is your target customer — individual researchers or institutions?"
CEO: "Business schools in Europe"
L1: ✅ Enough context gathered → Move to L3
```

---

#### **L2: Manual Research** (Human-in-the-loop)
**Role**: Data gathering by the CEO or research team

**What it does**:
- Waits for CEO to add data to the Knowledge Base (via Streamlit UI)
- Data can be: market reports, competitor analysis, financial benchmarks
- This data is used to "ground" the business plan (make it factual)

**Why it exists**:
- AI can't access paid databases (Passport, GlobalData, CB Insights)
- CEO has domain expertise and proprietary data
- Grounding reduces hallucination

**Example flow**:
```
System: "To improve quality, add market size data from Passport"
CEO uploads: "European EdTech market = €12B, growing 15% YoY"
System: ✅ Data added → Financial agent will use this
```

---

#### **L3: Feedback Agent** (`l3_feedback_agent.py`)
**Role**: Decision gatekeeper — presents summary and gets CEO approval

**What it does**:
1. Compiles all gathered information:
   - Original idea
   - Clarifying Q&A
   - Key assumptions
2. Summarizes it in 1 paragraph + biggest risk
3. Asks CEO: **"Yes / Adjust / Kill"**
4. If "Adjust" → goes back to L1 for more questions
5. If "Yes" → triggers Phase 2 (business plan generation)
6. If "Kill" → archives the session

**Why it exists**:
- CEO has final say on moving forward
- Prevents wasting compute on bad ideas
- Creates explicit decision audit trail

**Example flow**:
```
L3: "Summary: SaaS for academic researchers to pre-check manuscripts 
     before journal submission. Target: EU business schools. 
     Biggest risk: Unknown willingness-to-pay for B2B tools.
     
     Proceed? (Yes/Adjust/Kill)"
     
CEO: "Yes"
L3: ✅ Triggers Phase 2 pipeline
```

---

### Phase 1 Data Storage

**Supabase Tables**:
- `sessions`: Stores session state (NEEDS_CLARIFICATION → AWAITING_APPROVAL → APPROVED)
- `messages`: Every Telegram message logged with timestamp
- `assumptions`: Q&A pairs stored as structured data
- `decisions`: CEO's Yes/Adjust/Kill decision with reasoning
- `events_logs`: Audit trail of every agent action

**Redis**:
- Session state (fast reads)
- Pipeline triggers (e.g., `pipeline_trigger:session_id` → starts Phase 2)

---

## 🔶 Phase 2: Multi-Agent Business Plan Generation

Phase 2 is where the **actual business plan is generated**. This runs AFTER CEO approves in Phase 1.

### Architecture Pattern: Mother-Child Agent System

**Mother Agent**: Orchestrator that:
- Receives the approved idea from Phase 1
- Reads dependency rules (which sections depend on which)
- Assigns tasks to child agents
- Collects outputs
- Resolves conflicts between agents
- Compiles final business plan

**Child Agents**: 9 specialized agents, each responsible for ONE section of the business plan

---

### The 9 Child Agents

#### **1. Opportunity Analyst** (`opportunity_analyst.py`)
**Section**: 1 — Opportunity Analysis  
**Model**: Claude Sonnet (high capability)

**What it does**:
- Analyzes the customer problem
- Validates market opportunity
- Estimates market size (TAM/SAM/SOM)
- Identifies key assumptions about the problem

**Output fields**:
- `customer_problem`: What pain point does this solve?
- `market_size`: How big is the opportunity?
- `value_proposition`: Why would customers buy this?
- `assumptions_used`: What did we assume?
- `uncertainties`: What don't we know?
- `confidence_score`: high/medium/low

**Example output**:
```json
{
  "customer_problem": "Academic researchers face 40-60% manuscript rejection 
                       due to preventable formatting/methodology errors",
  "market_size": "€12M TAM in European academic publishing tools",
  "value_proposition": "Pre-submission diagnostics reduce rejection rate by 30%",
  "assumptions_used": [
    "Average manuscript preparation takes 3-6 months (CEO experience)",
    "Rejection costs 2-4 weeks of rework (industry reports)"
  ],
  "uncertainties": [
    "No data on willingness-to-pay for B2B academic tools"
  ],
  "confidence_score": "high"
}
```

---

#### **2. Environment Research** (`environment_research.py`)
**Section**: 3 — Market & Regulatory Environment  
**Model**: Claude Haiku (faster, cheaper for research tasks)

**What it does**:
- Researches competitors
- Identifies regulatory requirements (GDPR, AI Act, etc.)
- Analyzes market trends
- Lists barriers to entry

**Output fields**:
- `competitors`: List of direct competitors with positioning
- `regulations`: GDPR, AI Act compliance requirements
- `market_trends`: EdTech growth, AI adoption trends
- `barriers_to_entry`: What makes this hard to replicate?

**Why Haiku model**:
- Research tasks don't need deep reasoning
- Faster response = lower latency
- Cost-effective for data gathering

---

#### **3. Organisation Designer** (`organisation_designer.py`)
**Section**: 4 — Team & Organisation Structure  
**Model**: Claude Haiku

**What it does**:
- Defines initial team structure
- Lists required roles and skills
- Identifies hiring priorities
- Plans organizational scaling

**Output fields**:
- `initial_team`: Founder roles + first 3 hires
- `key_skills`: Technical, sales, domain expertise needed
- `hiring_plan`: When to hire which roles
- `org_scaling`: How team grows Year 1-3

---

#### **4. SWOT Synthesizer** (`swot_synthesizer.py`)
**Section**: 5 — SWOT Analysis  
**Model**: Claude Sonnet (requires synthesis across multiple inputs)

**What it does**:
- Reads outputs from Opportunity + Environment agents
- Synthesizes Strengths, Weaknesses, Opportunities, Threats
- Identifies strategic priorities

**Output fields**:
- `strengths`: Internal advantages (CEO expertise, network, etc.)
- `weaknesses`: Internal limitations (no tech team, limited capital)
- `opportunities`: External factors to exploit (market growth, funding)
- `threats`: External risks (competitors, regulation changes)

**Why Sonnet model**:
- SWOT requires cross-section synthesis
- Needs to reconcile conflicting information
- Higher reasoning capability needed

---

#### **5. Marketing Strategy** (`marketing_strategy.py`)
**Section**: 8 — Go-to-Market & Marketing  
**Model**: Claude Sonnet

**What it does**:
- Defines customer acquisition strategy
- Plans marketing channels (LinkedIn, conferences, partnerships)
- Estimates Customer Acquisition Cost (CAC)
- Designs launch strategy

**Output fields**:
- `go_to_market_strategy`: Phase 1/2/3 launch plan
- `customer_acquisition_channels`: LinkedIn, conferences, publisher partnerships
- `cac_estimate`: Estimated cost to acquire one customer
- `positioning`: How to differentiate from competitors

---

#### **6. Operations** (`operations.py`)
**Section**: 10 — Operations & Delivery  
**Model**: Claude Haiku

**What it does**:
- Plans product delivery infrastructure
- Defines operational processes (customer onboarding, support)
- Identifies key vendors and tools
- Plans scaling operations

**Output fields**:
- `delivery_model`: SaaS subscription with API access
- `infrastructure`: AWS/Vercel + Bedrock for AI
- `key_processes`: Onboarding, support, billing
- `scaling_plan`: How operations scale with customer growth

---

#### **7. Financial Modelling** (`financial_modelling.py`)
**Section**: 12 — Financial Projections  
**Model**: Claude Sonnet (requires complex calculations)

**What it does**:
- Builds 3-year revenue projections
- Models cost structure (COGS, S&M, R&D, overhead)
- Runs Monte Carlo simulation for risk scenarios
- Calculates key metrics (gross margin, burn rate, breakeven)

**Output fields**:
- `revenue_projections`: Year 1-3 revenue forecast
- `cost_structure`: Breakdown of expenses by category
- `simulation_results`: Monte Carlo risk distribution
- `key_metrics`: Gross margin, CAC, LTV, burn rate
- `funding_requirements`: How much capital needed?

**Special feature**: Calls `simulation/financial_sim.py` for Monte Carlo analysis

---

#### **8. Launch & Contingency** (`launch_contingency.py`)
**Section**: 13 — Launch Plan & Risk Mitigation  
**Model**: Claude Haiku

**What it does**:
- Plans MVP launch timeline
- Identifies risks and mitigation strategies
- Defines success metrics (KPIs)
- Plans pivot scenarios

**Output fields**:
- `launch_timeline`: MVP release date, milestones
- `risks`: Top 5 risks with likelihood/impact
- `mitigation_strategies`: How to reduce each risk
- `success_kpis`: Metrics to track (MRR, churn, NPS)

---

#### **9. Summary Agent** (`summary_agent.py`)
**Section**: Executive Summary  
**Model**: Claude Haiku

**What it does**:
- Reads ALL other sections
- Synthesizes into 1-page executive summary
- Highlights key metrics and biggest risks
- Creates investor-ready overview

**Output fields**:
- `executive_summary`: 200+ word overview
- `key_metrics`: Top 5 numbers (TAM, Year 1 revenue, gross margin, etc.)
- `biggest_risks`: Top 3 uncertainties
- `confidence_score`: Overall confidence in the plan

**Why it runs last**:
- Needs all other sections completed first
- Dependency: Sections 1,3,4,5,8,10,12,13 → Executive Summary

---

### How Child Agents Work (Technical Deep Dive)

Each child agent follows this pattern:

```python
class OpportunityAnalystAgent(BaseChildAgent):
    def __init__(self):
        self.model = "claude-sonnet-4-20250514"  # via AWS Bedrock
        self.system_prompt = """
        You are an opportunity analysis expert. Analyze the business idea 
        and return ONLY valid JSON with these exact fields:
        - customer_problem
        - market_size
        - value_proposition
        - assumptions_used
        - uncertainties
        - confidence_score
        """
    
    async def handle_request(self, idea_data: dict) -> dict:
        # 1. Validate input
        validate_input_schema(idea_data)
        
        # 2. Build prompt with CEO data
        user_prompt = f"""
        Business Idea: {idea_data['idea_summary']}
        CEO Context: {idea_data['ceo_assumptions']}
        
        Analyze the opportunity and return JSON.
        """
        
        # 3. Call LLM via Bedrock
        response = self._call_llm(user_prompt)
        
        # 4. Parse response (strip markdown, parse JSON)
        parsed = self._parse_llm_response(response)
        
        # 5. Validate output schema
        output = OpportunityOutput(**parsed)  # Pydantic validation
        
        # 6. If validation fails → escalate to Mother Agent
        if not output:
            self._escalate("Failed to generate valid opportunity analysis")
        
        return output.dict()
```

---

### Mother Agent's Role

The **Mother Agent** (`mother_agent.py`) is the orchestrator. Here's what it does:

#### **1. Task Distribution**
```yaml
# Reads dependency_map.yaml
sections:
  "1":  # Opportunity Analyst
    depends_on: []  # Can run immediately
  "5":  # SWOT
    depends_on: ["1", "3"]  # Needs Opportunity + Environment first
```

Mother Agent uses this map to:
- Run independent agents in parallel (1, 3, 4 can start together)
- Wait for dependencies before starting dependent agents
- Handle failures gracefully

#### **2. Intelligence Engine**
Mother Agent uses `IntelligenceEngine` to:
- Decide when to escalate to CEO
- Determine if more research is needed
- Choose which conflict resolution strategy to use

#### **3. Conflict Resolution**
If two agents give contradictory information:
```
Financial Agent: "TAM = €50M"
Opportunity Agent: "TAM = €12M"

Mother Agent:
1. Detects conflict (TAM mismatch)
2. Checks evidence quality for each claim
3. Escalates to CEO if critical
4. Otherwise: Uses higher-confidence estimate
```

#### **4. Quality Gates**
Before finalizing, Mother Agent runs:
- Coherence Auditor: Checks if sections contradict each other
- Quality Gate: Validates completeness (all required fields present?)
- Gap Analyzer: Lists what data is missing

---

### SPADE Messaging Protocol

Agents communicate via **XMPP messages** (SPADE framework):

```python
# Mother sends task to child agent
message = {
    "performative": "request",
    "task_id": "task_001",
    "session_id": "uuid-123",
    "content": {
        "idea_summary": "...",
        "ceo_assumptions": [...]
    }
}

# Child agent responds
response = {
    "performative": "inform",  # or "escalate" if failed
    "task_id": "task_001",
    "content": {
        "customer_problem": "...",
        "confidence_score": "high"
    }
}
```

**Performatives** (message types):
- `request`: Mother → Child (assign task)
- `inform`: Child → Mother (task complete)
- `escalate`: Child → Mother (task failed, need help)
- `propose`: Child → Child (negotiation between agents)
- `refuse`: Child → Mother (cannot complete task)

---

## 🔍 Evaluation Pipeline

The **evaluation pipeline** is a separate system that:
1. Takes the Phase 1 output (idea + CEO context)
2. Searches the web for real data
3. Runs Phase 2 agents with grounded data
4. Scores the output for quality
5. Delivers the business plan

### Components

#### **Data Declaration** (`demo_pipeline.py`)
Before running Phase 2, the system tells the CEO:

```
📋 DATA REQUIREMENTS

FETCHING AUTOMATICALLY (web search):
• EU AI Act compliance for academic SaaS
• GDPR procurement requirements Europe
• Academic publishing market trends 2025

REQUESTING FROM YOU (improves quality):
• Academic software market size Spain/EU
  → best source: Passport or GlobalData
• Competitor pricing and positioning data
  → best source: CB Insights or FACTIVA

Reply PROCEED to start, or SKIP to run with current data.
```

**Why this matters**:
- CEO knows exactly what data will improve quality
- System is transparent about what it DOESN'T know
- CEO can add data from paid databases the AI can't access

---

#### **Web Search Service** (`search_service.py`)
Uses **Tavily API** to search the web for:
- Market size estimates
- Competitor information
- Regulatory requirements
- Industry benchmarks

**Example**:
```
Query: "European EdTech market size 2025"
Result: [
  {
    "title": "EdTech Market Report 2025",
    "content": "European EdTech market reached €12.5B...",
    "url": "https://..."
  }
]
```

This data is passed to child agents to ground their outputs.

---

#### **Grounded Evaluation** (`run_grounded_eval.py`)
Runs Phase 2 agents with:
1. CEO data (from Phase 1)
2. Web search results
3. Knowledge base documents (uploaded by CEO)

Each agent's output includes:
- `assumptions_used`: What data sources were used?
- `uncertainties`: What couldn't be verified?
- `confidence_score`: How confident is this section?

---

#### **Gap Analysis**
After all agents complete, the system:
1. Aggregates all `uncertainties` from all agents
2. Categorizes by severity (high/medium/low)
3. Lists in final DOCX under "Data Gaps & Knowledge Limitations"

**Example gap**:
```
⚠️ [Financial Modelling] No Monte Carlo simulation run — 
   all projections deterministic [Severity: CRITICAL]
   
💡 Recommendation: Add comparable company data from CB Insights
```

---

## 🛠️ Technology Stack Deep Dive

### **1. SPADE (Agent Framework)**
**Why SPADE?**
- Built on XMPP (mature messaging protocol)
- Supports true multi-agent communication (not just function calls)
- Cyclic behaviors (agents can listen continuously)
- Async/await support (handles 9 agents concurrently)

**Alternative considered**: CrewAI, LangGraph
- **CrewAI**: Too sequential, harder to parallelize
- **LangGraph**: Graph-based but less mature for true MAS

---

### **2. Claude via AWS Bedrock**
**Why Claude?**
- Best-in-class for long-context reasoning (200K tokens)
- Excellent JSON adherence (critical for structured outputs)
- Two tiers: Sonnet (smart) + Haiku (fast)

**Why Bedrock instead of direct Anthropic API?**
- Enterprise compliance (GDPR, SOC2)
- AWS credits available
- Unified billing with other AWS services

**Model selection logic**:
```python
# Complex reasoning tasks
model = "claude-sonnet-4-20250514"  # Financial, SWOT, Opportunity

# Research and data gathering
model = "claude-haiku-4-5-20251001"  # Environment, Operations, Launch
```

---

### **3. Supabase (PostgreSQL)**
**Why Supabase?**
- Managed Postgres (no DB ops overhead)
- Row-level security (RLS) for multi-tenant
- Real-time subscriptions (future: live dashboard)
- Auto-generated APIs

**Schema design**:
```sql
-- Sessions: one per conversation
sessions(id, ceo_id, state, telegram_chat_id, created_at)

-- Messages: every Telegram message logged
messages(id, session_id, content, telegram_message_id, received_at)

-- Assumptions: Q&A from L1 agent
assumptions(id, session_id, question, answer, status)

-- Agent outputs: results from Phase 2
agent_outputs(id, session_id, section_number, output_json)

-- Events log: audit trail
events_logs(id, session_id, agent_name, action, timestamp)
```

---

### **4. Redis (Upstash)**
**Why Redis?**
- Fast key-value lookups (< 1ms latency)
- Session state caching (avoid DB reads)
- Pipeline triggers (pub/sub pattern)
- TTL support (sessions expire after 24h)

**Usage patterns**:
```python
# Session state
redis.set("session:uuid-123", json.dumps({"state": "APPROVED"}), ex=86400)

# Pipeline trigger
redis.set("pipeline_trigger:uuid-123", "1")  # Phase 2 starts

# PROCEED/SKIP response
redis.set("proceed_response:uuid-123", "proceed", ex=7200)  # 2 hour timeout
```

---

### **5. Telegram Bot API**
**Why Telegram?**
- CEO-friendly (mobile + desktop)
- Webhook support (instant delivery)
- Rich formatting (markdown, buttons)
- File uploads (DOCX delivery)

**Webhook pattern**:
```python
# Telegram sends POST to your server
@app.post("/telegram/webhook")
async def handle_webhook(request):
    data = await request.json()
    message = data["message"]
    
    # Pass to L0 Input Guard
    result = validate_message(message)
    if result["valid"]:
        # Route to L1 Clarity Agent
        ...
```

---

### **6. SimPy (Monte Carlo Simulation)**
**Why SimPy?**
- Discrete event simulation library
- Models probabilistic scenarios (best/base/worst case)
- Used by Financial Agent for revenue projections

**Example**:
```python
# Run 1000 scenarios
results = run_simulation(
    base_revenue=250000,
    growth_rate_range=(0.1, 0.3),  # 10-30% growth
    churn_rate_range=(0.05, 0.15),  # 5-15% churn
    runs=1000
)

# Output
{
  "mean_revenue_y3": 1200000,
  "confidence_95": (800000, 1600000),
  "probability_breakeven_y2": 0.78
}
```

---

### **7. Streamlit (Internal UI)**
**Why Streamlit?**
- Fast prototyping (no frontend code needed)
- Good for internal tools (not customer-facing)
- Real-time updates

**Features**:
- View all sessions
- Upload knowledge base documents
- Monitor agent logs
- Trigger manual pipeline runs

---

### **8. Python-DOCX (Document Export)**
**Why not PDF?**
- DOCX is editable (CEO can tweak the plan)
- Better for collaboration (comments, track changes)
- Professional styling with colors/tables/icons

---

## 🔄 Data Flow: End-to-End Journey

Let me trace a complete flow from CEO message to final business plan:

### **Step 1: CEO sends idea via Telegram**
```
CEO: "I want to build an AI tool that helps researchers avoid manuscript 
      rejection by pre-checking formatting and methodology errors"
```

**What happens**:
1. Telegram webhook receives message
2. L0 Input Guard validates sender, creates session UUID
3. Message logged to Supabase `messages` table
4. Session created with state = `NEEDS_CLARIFICATION`
5. Routed to L1 Clarity Agent

---

### **Step 2: L1 asks clarifying questions**
```
L1: "Who is your target customer — individual researchers or institutions?"
```

**What happens**:
1. L1 reads CEO profile from Supabase (knows CEO's background)
2. Checks existing assumptions (have we asked this before?)
3. Generates ONE question using Claude Haiku
4. Sends via Telegram
5. Stores question in `assumptions` table with status = `pending`

**CEO responds**:
```
CEO: "Business schools in Europe — they'll pay for institutional licenses"
```

**What happens**:
1. L0 validates message (same session UUID)
2. L1 receives answer
3. Updates assumption: status = `active`, answer = "Business schools in Europe..."
4. Checks: Do we have enough info? (3 questions max)
5. If yes → route to L3. If no → ask another question.

---

### **Step 3: L3 presents summary for approval**
```
L3: "📋 SUMMARY

Business Idea: AI-powered manuscript pre-submission diagnostics for academic 
researchers to reduce rejection rates.

Target Customer: European business schools (institutional B2B)

Key Assumptions:
• Average manuscript takes 3-6 months to prepare
• Rejection costs 2-4 weeks of rework
• Schools will pay €5K/year for institutional license

Biggest Risk: Unknown willingness-to-pay for B2B academic tools (no market data)

DECISION: Proceed with business plan? (Yes/Adjust/Kill)"
```

**CEO responds**: `Yes`

**What happens**:
1. L3 logs decision to `decisions` table (status = `approved`)
2. Updates session state = `APPROVED`
3. Writes trigger to Redis: `pipeline_trigger:uuid-123`
4. Demo pipeline poller detects trigger → starts evaluation pipeline

---

### **Step 4: Data declaration**
```
📋 BEFORE I START — DATA REQUIREMENTS

FETCHING AUTOMATICALLY (web search):
• EU AI Act compliance for academic SaaS
• Academic publishing market trends 2025
• B2B SaaS gross margin benchmarks

REQUESTING FROM YOU (improves quality):
• Academic software market size Spain/EU
  → best source: Passport or GlobalData
• Competitor pricing and positioning
  → best source: CB Insights or FACTIVA

Reply PROCEED to start, or SKIP to run with current data.
```

**What happens**:
1. Demo pipeline calls `_build_data_declaration()` with LLM
2. LLM analyzes the idea and generates specific data requests
3. Sends message via Telegram
4. Waits for PROCEED/SKIP response (polls Redis key `proceed_response:uuid-123`)
5. Timeout = 2 hours (auto-proceed if no response)

**CEO responds**: `PROCEED`

---

### **Step 5: Web search (automatic)**
```python
# Search for required data
queries = [
    "EU AI Act compliance academic SaaS",
    "GDPR procurement requirements Europe",
    "Academic publishing market size 2025"
]

for query in queries:
    results = tavily_search(query, max_results=3)
    # Store in knowledge_base table
```

---

### **Step 6: Phase 2 agents run**

**Mother Agent starts**:
```python
# Read dependency map
dependencies = {
    "1": [],  # Opportunity — no deps
    "3": [],  # Environment — no deps
    "4": [],  # Organisation — no deps
    "5": ["1", "3"],  # SWOT needs Opportunity + Environment
    ...
}

# Start independent agents in parallel
await asyncio.gather(
    run_agent("opportunity_analyst", idea_data),
    run_agent("environment_research", idea_data),
    run_agent("organisation_designer", idea_data),
)

# Wait for results, then start dependent agents
opportunity_result = await get_result("1")
environment_result = await get_result("3")

await run_agent("swot_synthesizer", {
    "idea_data": idea_data,
    "opportunity": opportunity_result,
    "environment": environment_result
})
```

**Each agent**:
1. Receives task via SPADE message
2. Reads CEO data + web search results
3. Calls Claude via Bedrock
4. Parses JSON response
5. Validates output schema (Pydantic)
6. Returns result or escalates if failed

---

### **Step 7: Gap analysis**
```python
# Aggregate all uncertainties
all_gaps = []
for section in results["sections"]:
    uncertainties = section["output"]["uncertainties"]
    all_gaps.extend(uncertainties)

# Categorize by severity
critical_gaps = [g for g in all_gaps if g["severity"] == "critical"]
high_gaps = [g for g in all_gaps if g["severity"] == "high"]
medium_gaps = [g for g in all_gaps if g["severity"] == "medium"]
```

---

### **Step 8: Output delivery**

**Text summary via Telegram**:
```
✅ BUSINESS PLAN READY

Opportunity Score: 7.5/10
Overall Confidence: MEDIUM-HIGH

Key Findings:
• TAM: €12M (European academic tools)
• Target: 50 business schools Year 1
• Revenue projection: €250K Year 1 → €1.2M Year 3
• Biggest risk: Unverified willingness-to-pay

📄 Full report attached as DOCX
```

**DOCX export**:
```python
# Generate styled document
docx_path = export_to_docx(results)

# Upload to Telegram
await send_document(
    chat_id=chat_id,
    file_path=docx_path,
    caption="📄 Complete business plan with all sections"
)
```

---

## 🤖 All Agents Explained (Summary Table)

| Agent | Phase | Purpose | Model | Output |
|-------|-------|---------|-------|--------|
| **L0 Input Guard** | 1 | Validate sender, prevent duplicates, create sessions | N/A | Valid message + session UUID |
| **L1 Clarity Agent** | 1 | Ask clarifying questions (max 3) | Haiku | Q&A stored as assumptions |
| **L2 Manual Research** | 1 | Human adds data to Knowledge Base | N/A | Uploaded documents |
| **L3 Feedback Agent** | 1 | Present summary, get CEO approval | Haiku | Yes/Adjust/Kill decision |
| **Mother Agent** | 2 | Orchestrate child agents, resolve conflicts | Sonnet | Full business plan |
| **Opportunity Analyst** | 2 | Market opportunity + customer problem | Sonnet | Section 1 JSON |
| **Environment Research** | 2 | Competitors, regulations, trends | Haiku | Section 3 JSON |
| **Organisation Designer** | 2 | Team structure, hiring plan | Haiku | Section 4 JSON |
| **SWOT Synthesizer** | 2 | Strengths/Weaknesses/Opportunities/Threats | Sonnet | Section 5 JSON |
| **Marketing Strategy** | 2 | Go-to-market, CAC, positioning | Sonnet | Section 8 JSON |
| **Operations** | 2 | Delivery model, infrastructure | Haiku | Section 10 JSON |
| **Financial Modelling** | 2 | Revenue projections, Monte Carlo sim | Sonnet | Section 12 JSON |
| **Launch & Contingency** | 2 | Launch plan, risks, mitigation | Haiku | Section 13 JSON |
| **Summary Agent** | 2 | Executive summary | Haiku | Exec summary JSON |
| **Coherence Auditor** | 2 | Check for contradictions | Sonnet | Conflict report |
| **Devil's Advocate** | 2 | Challenge assumptions | Sonnet | Risk analysis |
| **Quality Gate** | 2 | Validate completeness | N/A | Pass/fail + gaps |

---

## 🎯 Why This Approach Works

### **1. Specialization → Better Quality**
- Each agent focuses on ONE task
- Like hiring 10 expert consultants vs. 1 generalist
- Financial agent only does finance → becomes expert at it

### **2. Explicit Uncertainty Tracking**
- Every agent flags what it DOESN'T know
- Traditional AI: hallucinates fake data
- This system: "I don't have market size data — suggest using Passport"

### **3. Grounding with Real Data**
- Web search (automatic)
- CEO uploads (manual)
- Knowledge base retrieval
- Result: Factual, verifiable business plans

### **4. Memory System**
- Supabase: Permanent storage (every Q&A, decision, assumption)
- Redis: Fast cache (session state, triggers)
- Result: Context never lost, even across multiple sessions

### **5. Transparency**
- Every agent action logged to `events_logs`
- CEO sees confidence scores for every section
- Gaps explicitly listed with recommendations

### **6. CEO Control**
- L3 approval gate (CEO decides to proceed)
- Data declaration (CEO knows what data improves quality)
- Adjust/Kill options (CEO can redirect or stop)

### **7. Scalability**
- SPADE handles concurrent agents (9 run in parallel)
- Redis caching reduces DB load
- Bedrock scales automatically (AWS managed)

---

## 📊 Real-World Impact

### **Before this system**:
- CEO hires consultants: €10K-50K, 4-8 weeks
- Gets a plan with hidden assumptions
- No way to verify data sources
- Expensive to iterate or adjust

### **With this system**:
- CEO sends idea via Telegram: Free, 30 minutes
- Gets a plan with explicit confidence levels
- Every assumption documented and sourced
- Can re-run with updated data instantly

### **Key metrics**:
- **Speed**: 30 min vs. 4 weeks
- **Cost**: $50 in API costs vs. €10K-50K consulting fees
- **Transparency**: 100% assumptions documented vs. hidden in prose
- **Iterability**: Instant re-runs vs. weeks of back-and-forth

---

## 🚀 Next Steps (Future Enhancements)

### **Phase 3: Advanced Intelligence**
- Multi-round negotiation between agents (agents debate, not just report)
- Council voting (3 agents vote on critical decisions)
- Learning from past sessions (vector memory)
- Auto-escalation rules (when to ask CEO vs. auto-resolve)

### **Phase 4: Production Hardening**
- Multi-tenant support (multiple CEOs using the system)
- Payment integration (Stripe for subscriptions)
- Advanced dashboard (Notion/Airtable sync)
- API for third-party integrations

---

## 📚 Key Files Reference

```
multi-agent-system/
├── agents/
│   ├── phase1/
│   │   ├── l0_input_guard.py       # Message validation
│   │   ├── l1_clarity_agent.py     # Question generator
│   │   └── l3_feedback_agent.py    # Approval gate
│   └── phase2/
│       ├── mother_agent.py         # Orchestrator
│       ├── opportunity_analyst.py  # Section 1
│       ├── environment_research.py # Section 3
│       ├── swot_synthesizer.py     # Section 5
│       ├── marketing_strategy.py   # Section 8
│       ├── financial_modelling.py  # Section 12
│       └── summary_agent.py        # Executive summary
├── evaluation/
│   ├── run_grounded_eval.py        # Runs Phase 2 with grounding
│   ├── demo_pipeline.py            # Phase 1 → Eval → Phase 2 connector
│   └── export_docx.py              # Professional document export
├── memory/
│   ├── supabase_client.py          # PostgreSQL queries
│   └── redis_client.py             # Session cache
├── tools/
│   ├── telegram_handler.py         # Send/receive messages
│   └── search_service.py           # Web search via Tavily
├── database/
│   └── schema.sql                  # Supabase table definitions
└── main.py                         # Entry point
```

---

## 🎓 Conclusion

This system is a **multi-agent AI consulting firm in a box**. Instead of one AI trying to do everything, it's an **ensemble of specialized agents** that:
- Collaborate like a real team
- Check each other's work
- Explicitly track what they don't know
- Ground outputs in real data
- Give the CEO full control and transparency

It solves the core problems of single-agent AI (hallucination, shallow analysis, no memory) by using **true multi-agent architecture** with specialized roles, explicit communication protocols, and verification layers.

The result: **Production-quality business plans in 30 minutes** that are transparent, verifiable, and iterative — at a fraction of the cost of traditional consulting.
