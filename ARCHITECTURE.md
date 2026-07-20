# 🏗️ System Architecture — Visual Guide

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CEO (Alex)                                   │
│                    Web Interface Mobile/Desktop                           │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             │ Messages (HTTPS Webhook)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT API                                │
│                   webhook.py (FastAPI)                               │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             │ Route to Phase 1
                             ▼
╔═════════════════════════════════════════════════════════════════════╗
║                         PHASE 1 PIPELINE                             ║
║                  (Context Gathering & Approval)                      ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌─────────┐║
║  │ L0 Guard   │───▶│ L1 Clarity │───▶│ L2 Research│───▶│L3 Feedback│║
║  │            │    │            │    │            │    │         │║
║  │ • Validate │    │ • Ask Q's  │    │ • Human    │    │ • Approve││
║  │ • Dedup    │    │ • Max 3    │    │   adds data│    │ • Yes/   ││
║  │ • Create   │    │ • Store    │    │ • Knowledge│    │   Adjust/││
║  │   session  │    │   as       │    │   Base     │    │   Kill   ││
║  │            │    │   assumptions│  │            │    │         │║
║  └────────────┘    └────────────┘    └────────────┘    └─────────┘║
║                                                              │       ║
║                                                              │       ║
║  Storage: Supabase (sessions, messages, assumptions)         │       ║
║           Redis (state cache)                                │       ║
╚══════════════════════════════════════════════════════════════╪═══════╝
                                                               │
                                                               │ If "Yes"
                                                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       REDIS TRIGGER                                  │
│              pipeline_trigger:session_id = "1"                       │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             │ Demo Pipeline Poller detects
                             ▼
╔═════════════════════════════════════════════════════════════════════╗
║                      EVALUATION PIPELINE                             ║
║                  (Data Gathering & Grounding)                        ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  1. Read session data from Supabase                                  ║
║  2. Build idea dict (idea + CEO Q&A)                                 ║
║  3. Generate data declaration (LLM)                                  ║
║  4. Send to CEO via Web Interface                                         ║
║  5. Wait for PROCEED/SKIP (2 hour timeout)                           ║
║  6. Run web searches (Tavily):                                       ║
║     • Market size                                                    ║
║     • Competitors                                                    ║
║     • Regulations                                                    ║
║     • Benchmarks                                                     ║
║  7. Retrieve Knowledge Base docs                                     ║
║  8. Pass grounded data to Phase 2                                    ║
║                                                                       ║
╚══════════════════════════════════════════════════════════════╪═══════╝
                                                               │
                                                               │ Grounded data
                                                               ▼
╔═════════════════════════════════════════════════════════════════════╗
║                         PHASE 2 PIPELINE                             ║
║              (Multi-Agent Business Plan Generation)                  ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║                     ┌───────────────────────┐                        ║
║                     │   MOTHER AGENT        │                        ║
║                     │   (Orchestrator)      │                        ║
║                     │                       │                        ║
║                     │ • Task distribution   │                        ║
║                     │ • Dependency mgmt     │                        ║
║                     │ • Conflict resolution │                        ║
║                     │ • Quality gates       │                        ║
║                     └───────────┬───────────┘                        ║
║                                 │                                    ║
║        ┌────────────────────────┼────────────────────────┐           ║
║        │                        │                        │           ║
║        ▼                        ▼                        ▼           ║
║  ┌──────────┐            ┌──────────┐            ┌──────────┐       ║
║  │ Agent 1  │            │ Agent 3  │            │ Agent 4  │       ║
║  │Opportunity│            │Environment│           │Organisation│    ║
║  │ Analyst  │            │ Research │            │ Designer │       ║
║  │ (Sonnet) │            │ (Haiku)  │            │ (Haiku)  │       ║
║  └─────┬────┘            └─────┬────┘            └─────┬────┘       ║
║        │                       │                       │            ║
║        └───────────────┬───────┴───────────────────────┘            ║
║                        │ Results                                    ║
║                        ▼                                            ║
║                  ┌──────────┐                                        ║
║                  │ Agent 5  │                                        ║
║                  │   SWOT   │                                        ║
║                  │Synthesizer│                                       ║
║                  │ (Sonnet) │                                        ║
║                  └─────┬────┘                                        ║
║                        │                                            ║
║        ┌───────────────┼───────────────┐                            ║
║        ▼               ▼               ▼                            ║
║  ┌──────────┐    ┌──────────┐    ┌──────────┐                      ║
║  │ Agent 8  │    │Agent 10  │    │Agent 12  │                      ║
║  │Marketing │    │Operations│    │Financial │                      ║
║  │ Strategy │    │ (Haiku)  │    │Modelling │                      ║
║  │ (Sonnet) │    │          │    │ (Sonnet) │                      ║
║  └─────┬────┘    └─────┬────┘    └─────┬────┘                      ║
║        │               │               │                            ║
║        └───────────────┼───────────────┘                            ║
║                        ▼                                            ║
║                  ┌──────────┐                                        ║
║                  │Agent 13  │                                        ║
║                  │ Launch & │                                        ║
║                  │Contingency│                                       ║
║                  │ (Haiku)  │                                        ║
║                  └─────┬────┘                                        ║
║                        │                                            ║
║                        ▼                                            ║
║                  ┌──────────┐                                        ║
║                  │Summary   │                                        ║
║                  │  Agent   │                                        ║
║                  │ (Haiku)  │                                        ║
║                  └─────┬────┘                                        ║
║                        │                                            ║
╚════════════════════════╪═══════════════════════════════════════════╝
                         │ All sections complete
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     QUALITY & VERIFICATION                           │
│  • Coherence Auditor: Check contradictions                           │
│  • Quality Gate: Validate completeness                               │
│  • Gap Analyzer: List missing data                                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             │ Final business plan JSON
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        OUTPUT DELIVERY                               │
│  1. Compile text summary                                             │
│  2. Export to styled DOCX                                            │
│  3. Send summary via Web Interface                                        │
│  4. Upload DOCX via Web Interface                                         │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CEO RECEIVES                                 │
│  • Text summary with confidence scores                               │
│  • Professional DOCX with colors, tables, icons                      │
│  • Gap analysis with recommendations                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Communication Flow (SPADE)

```
Mother Agent                Child Agent (e.g., Opportunity)
    │                              │
    │  ───request───────────────▶  │
    │  {                           │
    │    "idea": "...",            │
    │    "ceo_data": [...]         │
    │  }                           │
    │                              │
    │                              │  1. Validate input
    │                              │  2. Call Claude (Bedrock)
    │                              │  3. Parse JSON response
    │                              │  4. Validate output
    │                              │
    │  ◀──inform──────────────────  │
    │  {                           │
    │    "customer_problem": "...",│
    │    "confidence": "high"      │
    │  }                           │
    │                              │
    ▼                              ▼
Store in                     Log to events_logs
agent_outputs table
```

---

## Data Architecture

### **Supabase Tables**

```
┌──────────────────────────────────────────────────────────────┐
│                         sessions                              │
├────────────────────┬─────────────────────────────────────────┤
│ id (UUID PK)       │ Unique session identifier               │
│ ceo_id (UUID FK)   │ Links to profiles.id                    │
│ state (ENUM)       │ NEEDS_CLARIFICATION, APPROVED, etc.     │
│ Web Interface_chat_id   │ For sending replies                     │
│ created_at         │ Session start time                      │
│ updated_at         │ Last activity                           │
└────────────────────┴─────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                         messages                              │
├────────────────────┬─────────────────────────────────────────┤
│ id (UUID PK)       │ Unique message ID                       │
│ session_id (FK)    │ Links to sessions.id                    │
│ content (TEXT)     │ Message text                            │
│ Web Interface_message_id│ For deduplication                       │
│ received_at        │ Timestamp                               │
│ from_user (JSONB)  │ User metadata                           │
└────────────────────┴─────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                       assumptions                             │
├────────────────────┬─────────────────────────────────────────┤
│ id (UUID PK)       │ Unique assumption ID                    │
│ session_id (FK)    │ Links to sessions.id                    │
│ question (TEXT)    │ L1 generated question                   │
│ answer (TEXT)      │ CEO's response                          │
│ status (ENUM)      │ pending, active, rejected               │
│ confidence (ENUM)  │ high, medium, low                       │
│ source (TEXT)      │ ceo_experience, web_search, etc.        │
└────────────────────┴─────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                        decisions                              │
├────────────────────┬─────────────────────────────────────────┤
│ id (UUID PK)       │ Unique decision ID                      │
│ session_id (FK)    │ Links to sessions.id                    │
│ summary (TEXT)     │ L3 generated summary                    │
│ biggest_risk (TEXT)│ Top identified risk                     │
│ ceo_response (ENUM)│ yes, adjust, kill                       │
│ status (ENUM)      │ pending, approved, rejected             │
│ version (INT)      │ For Adjust → resubmit                   │
└────────────────────┴─────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                      agent_outputs                            │
├────────────────────┬─────────────────────────────────────────┤
│ id (UUID PK)       │ Unique output ID                        │
│ session_id (FK)    │ Links to sessions.id                    │
│ section_number     │ 1, 3, 4, 5, 8, 10, 12, 13               │
│ output_json (JSONB)│ Full agent output                       │
│ confidence (ENUM)  │ high, medium-high, medium, low          │
│ input_tokens (INT) │ LLM usage                               │
│ output_tokens (INT)│ LLM usage                               │
│ created_at         │ Generation timestamp                    │
└────────────────────┴─────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                       events_logs                             │
├────────────────────┬─────────────────────────────────────────┤
│ id (UUID PK)       │ Unique event ID                         │
│ session_id (FK)    │ Links to sessions.id                    │
│ agent_name (TEXT)  │ l0_guard, l1_clarity, mother, etc.      │
│ action (TEXT)      │ validate_message, generate_question     │
│ input_summary      │ Truncated input                         │
│ output_summary     │ Truncated output                        │
│ state_before       │ Session state before action             │
│ state_after        │ Session state after action              │
│ timestamp          │ Event time                              │
└────────────────────┴─────────────────────────────────────────┘
```

### **Redis Keys**

```
session:{session_id}               → JSON of session state (TTL 24h)
pipeline_trigger:{session_id}      → "1" when L3 approves (consumed once)
proceed_response:{session_id}      → "proceed" or "skip" (TTL 2h)
```

---

## Agent Decision Matrix

| Agent | When to Use Sonnet | When to Use Haiku |
|-------|-------------------|-------------------|
| Opportunity Analyst | ✅ Always (needs deep reasoning) | ❌ |
| Environment Research | ❌ | ✅ Always (data gathering) |
| Organisation Designer | ❌ | ✅ Always (structured output) |
| SWOT Synthesizer | ✅ Always (synthesis task) | ❌ |
| Marketing Strategy | ✅ Always (strategic reasoning) | ❌ |
| Operations | ❌ | ✅ Always (process design) |
| Financial Modelling | ✅ Always (complex calculations) | ❌ |
| Launch & Contingency | ❌ | ✅ Always (risk checklists) |
| Summary Agent | ❌ | ✅ Always (summarization) |

**Rule of thumb**:
- **Sonnet**: Reasoning, synthesis, strategy, finance
- **Haiku**: Research, structured data, checklists, summaries

---

## Dependency Graph (Phase 2)

```
Start
  │
  ├─▶ Agent 1 (Opportunity) ─┐
  │                           │
  ├─▶ Agent 3 (Environment) ─┤
  │                           ├─▶ Agent 5 (SWOT)
  ├─▶ Agent 4 (Organisation)─┘
  │
  └─▶ Agent 5 completes
       │
       ├─▶ Agent 8 (Marketing) ─┐
       │                          │
       ├─▶ Agent 10 (Operations)─┤
       │                          ├─▶ Agent 13 (Launch)
       └─▶ Agent 12 (Financial) ─┘
            │
            └─▶ Agent 13 completes
                 │
                 └─▶ Summary Agent
                      │
                      └─▶ DONE
```

**Key insight**: Maximum parallelization at each level
- Level 1: 3 agents run in parallel (1, 3, 4)
- Level 2: 1 agent (5 — waits for 1, 3)
- Level 3: 3 agents run in parallel (8, 10, 12)
- Level 4: 1 agent (13 — waits for 8, 10, 12)
- Level 5: 1 agent (Summary — waits for all)

**Total time**: ~5-7 minutes (not 9× sequential)

---

## Error Handling & Escalation

```
Child Agent
    │
    ├─ Try: Parse LLM response
    │   │
    │   ├─ Success → Return to Mother
    │   │
    │   └─ Failure → Parse fallback defaults
    │       │
    │       ├─ Success → Return with low confidence
    │       │
    │       └─ Failure → Escalate to Mother
    │
Mother Agent receives escalation
    │
    ├─ Check: Is this a critical section?
    │   │
    │   ├─ Yes → Retry with different prompt
    │   │   │
    │   │   ├─ Success → Continue
    │   │   └─ Failure → Notify CEO
    │   │
    │   └─ No → Use fallback section
    │       │
    │       └─ Flag as gap in final report
```

---

## Security & Permissions

### **Supabase Row-Level Security (RLS)**

```sql
-- Only CEO can read their own data
CREATE POLICY "Users can read own sessions"
ON sessions
FOR SELECT
USING (auth.uid() = ceo_id);

-- Agents use service role key (bypasses RLS)
-- CEO uses personal JWT (RLS enforced)
```

### **Environment Variables**

```bash
# Required
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJhb... (service role key)
REDIS_URL=redis://default:xxx@xxx.upstash.io:6379
TELEGRAM_BOT_TOKEN=7162589386:AAE...
AWS_BEDROCK_REGION=us-east-1
CLAUDE_SONNET_MODEL=claude-sonnet-4-20250514
CLAUDE_HAIKU_MODEL=claude-haiku-4-5-20251001

# Optional
TAVILY_API_KEY=tvly-xxx (for web search)
```

---

## Cost Analysis

### **Per Business Plan (Typical)**

| Component | Usage | Cost |
|-----------|-------|------|
| Claude Sonnet | ~100K input + 30K output | $12 |
| Claude Haiku | ~80K input + 20K output | $0.80 |
| Tavily search | 15 queries × 3 results | $0.15 |
| Supabase | 100 DB ops | Free tier |
| Redis | 50 ops | Free tier |
| **Total** | | **~$13** |

### **Compared to Traditional Consulting**

| Method | Cost | Time | Transparency |
|--------|------|------|--------------|
| McKinsey/Bain | €50K-200K | 8-12 weeks | Low (black box) |
| Boutique consultants | €10K-50K | 4-8 weeks | Medium |
| Freelance consultant | €5K-15K | 2-4 weeks | High |
| **This system** | **$13** | **30 min** | **Very high** |

**ROI**: 1000× cost reduction, 500× time reduction

---

## Monitoring & Observability

### **What gets logged**

1. **Events log** (Supabase): Every agent action
2. **Agent messages** (Supabase): SPADE messages between agents
3. **LLM calls** (Supabase): Prompt hash, tokens, latency
4. **Errors** (Supabase): Stack trace, context, recovery action

### **Key metrics to track**

- **Success rate**: % of sessions that complete without escalation
- **Confidence distribution**: % high vs. low confidence sections
- **Time to completion**: Median time from L0 → final output
- **Token usage**: Average tokens per business plan
- **Gap count**: Average # of uncertainties per plan

---

## Scalability Considerations

### **Current limits (single instance)**

- **Concurrent sessions**: 10 (limited by SPADE agents)
- **Agents per session**: 9 (Phase 2)
- **Max latency**: ~5-7 minutes (Phase 2 completion)

### **Scaling strategies**

1. **Horizontal scaling**: Multiple SPADE server instances
2. **Queue-based**: Use Redis queue for session distribution
3. **Serverless agents**: Convert agents to AWS Lambda (not SPADE)
4. **Database sharding**: Split sessions across multiple Supabase instances

### **Bottlenecks to watch**

- **Bedrock rate limits**: 10K requests/min (contact AWS for increase)
- **Redis memory**: 10K sessions = ~50MB (Upstash free tier = 256MB)
- **Supabase connection pool**: 60 connections (upgrade plan if needed)

---

This architecture is designed for **production-grade reliability** while maintaining **research-grade flexibility**. Every component can be swapped, scaled, or upgraded independently.
