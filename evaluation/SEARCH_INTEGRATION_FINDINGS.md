# Search Integration Findings — 2026-06-03

## Executive Summary

**Search service was NOT called during this eval run** because the grounded eval bypasses the agent layer entirely and calls the IntelligenceEngine directly.

### Key Findings

1. ✅ **Section 13 now passes** (connection retry fix works)
2. ✅ **100% parse rate** (9/9 sections)
3. ❌ **Search service never invoked** (architectural issue)
4. ⚠️ **All confidence scores = low** (no improvement from search)
5. ⚠️ **Sources appear to be hallucinated** (no real URLs or live data)

---

## 1. Search Service Was Not Called

**Evidence:**
- Zero log lines containing "search", "tavily", or "query" in eval output
- The grounded eval calls `engine.reason_and_produce()` directly
- Search service is wired into `agent.handle_request()` methods
- The eval never hits the agent layer where search lives

**Code path:**
```python
# Current eval flow:
run_grounded_eval.py
  └─> engine.reason_and_produce()  # IntelligenceEngine
      └─> Bedrock API directly
      └─> NEVER calls agent methods

# Where search lives:
agents/phase2/environment_research.py
  └─> handle_request()
      └─> search_for_section()  # <-- never reached
      └─> _call_llm()
```

**Verification:**
- Imports exist: `from services.search_service import search_for_section`
- Search queries defined: `SEARCH_QUERIES = {"3": [...], "8": [...], ...}`
- But `handle_request()` is never called by the eval harness

---

## 2. Section 13 Now Passes ✅

**Previous run (June 2):** Section 13 failed with connection timeout

**Current run (June 3):**
- ✅ Parse: True
- ✅ Latency: 146.0s
- ✅ Confidence: low
- ✅ No errors

**Fix applied:** Bedrock client retry config (`max_attempts=0` → connection timeout handled)

---

## 3. Overall Parse Rate & Confidence

| Metric | Value |
|--------|-------|
| Parse rate | 9/9 (100%) |
| Total tokens | 210,646 |
| Total latency | 1,590.5s (26.5 min) |
| Errors | 0 |
| Confidence distribution | low: 9 |

**Comparison to previous run (June 2):**
- Previous: 8/9 parsed (section 13 failed)
- Current: 9/9 parsed ✅
- Confidence unchanged: all "low" in both runs

---

## 4. Output Quality — Section 3 Environment Research

**Sample from current run:**

```json
{
  "factor": "POLITICAL: EU Research Integrity Directive (2019) + Spain's National Plan...",
  "source": "AACSB Accreditation Handbook (2023); EQUIS Standards (2020); Spain Ministry of Science directive.",
  "confidence": "HIGH"
}
```

**Previous run (June 2):**

```json
{
  "factor": "EU research-integrity regulation and emerging AI governance in research...",
  "source": "AACSB Standards 2023; EQUIS 5th Edition (2023)"
}
```

**Observations:**
- Both runs cite similar sources (AACSB, EQUIS, etc.)
- No URLs, no arxiv links, no DOIs
- Sources appear to be **hallucinated** (LLM-generated, not retrieved)
- No evidence of real market data (revenue numbers, TAM, etc.) that wasn't in CEO data
- Current run is slightly more verbose but not more grounded

---

## 5. Why Search Didn't Work

### Architectural Issue

The grounded eval has two layers:

1. **IntelligenceEngine** (`services/intelligence_engine.py`)
   - Direct Bedrock API wrapper
   - Used by the eval harness
   - No search integration

2. **Agent layer** (`agents/phase2/*.py`)
   - SPADE agents with `handle_request()` methods
   - Contains search service calls
   - Used in production pipeline only

### Options to Fix

**Option A: Wire search into IntelligenceEngine**
```python
# In intelligence_engine.py
async def reason_and_produce(...):
    # Add search call here
    if section_number in ["1", "3", "8", "12"]:
        search_results = search_for_section(section_number, queries)
        # Inject into user message
```

**Option B: Change eval to call agents directly**
```python
# In run_grounded_eval.py
agent = EnvironmentResearchAgent(...)
output = await agent.handle_request(input_data)
# Instead of engine.reason_and_produce()
```

**Option C: Add search calls in eval script**
```python
# In run_grounded_eval.py, before each section:
if section_num in ["1", "3", "8", "12"]:
    search_results = search_for_section(section_num, QUERIES[section_num])
    input_data["live_market_data"] = search_results
```

---

## 6. Next Steps

### Immediate
1. **Do NOT re-score yet** — output quality unchanged from previous run
2. **Decide on search integration approach** (A/B/C above)
3. **Verify search works** by calling it manually first

### Before Next Eval Run
- Wire search into eval path (pick Option A/B/C)
- Add logging to verify search is called
- Check that search results appear in agent prompts
- Verify output references real URLs/data

### Then
- Re-run grounded eval with search active
- Compare output quality to this baseline
- Only re-score if output shows real improvement

---

## Files Referenced

- `evaluation/run_grounded_eval.py` — eval harness
- `services/intelligence_engine.py` — IE layer (no search)
- `agents/phase2/environment_research.py` — agent layer (has search)
- `services/search_service.py` — Tavily integration
- `evaluation/results/grounded_epistemic_os_20260603_060414.json` — current run
- `evaluation/results/grounded_epistemic_os_20260602_063506.json` — previous run
