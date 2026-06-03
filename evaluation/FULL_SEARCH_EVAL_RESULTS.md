# Full Grounded Eval with Search — Results (2026-06-03)

**Run file:** `grounded_epistemic_os_20260603_071755.json`  
**Status:** ✅ Complete — 9/9 sections parsed, 0 errors

---

## 1. Search Activity Logs — All 4 Sections

### Section 1: Opportunity Analyst

```
[Section 1] Search initiated: 'academic manuscript validation software market size 2025'
[Section 1] Search complete: 5 results, freshness: unknown=5

[Section 1] Search initiated: 'epistemic validation tools universities Europe market'
[Section 1] Search complete: 5 results, freshness: unknown=5

[Section 1] Search initiated: 'pre-submission research diagnostics SaaS competitors'
[Section 1] Search complete: 5 results, freshness: unknown=5

[Search] Section 1: injected 3080 chars of live market data
```

**Latency:** 213.4s  
**Tokens:** 20,995  
**Confidence:** low

---

### Section 3: Environment Research

```
[Section 3] Search initiated: 'EU AI Act academic research software compliance 2025'
[Section 3] Search complete: 5 results, freshness: unknown=5

[Section 3] Search initiated: 'GDPR SaaS academic procurement requirements Europe'
[Section 3] Search complete: 5 results, freshness: unknown=5

[Section 3] Search initiated: 'European academic publishing market regulation 2025'
[Section 3] Search complete: 5 results, freshness: unknown=5

[Search] Section 3: injected 2969 chars of live market data
```

**Latency:** 155.6s  
**Tokens:** 26,374  
**Confidence:** low

---

### Section 8: Marketing Strategy

```
[Section 8] Search initiated: 'institutional SaaS pricing universities Europe 2025'
[Section 8] Search complete: 5 results, freshness: unknown=5

[Section 8] Search initiated: 'academic software procurement business schools budget'
[Section 8] Search complete: 5 results, freshness: unknown=5

[Section 8] Search initiated: 'research quality management tools university pricing'
[Section 8] Search complete: 5 results, freshness: unknown=5

[Search] Section 8: injected 3203 chars of live market data
```

**Latency:** 543.8s (longest — needed re-production with gaps)  
**Tokens:** 39,863 (highest)  
**Confidence:** **high** ⭐ (only section with high confidence)

---

### Section 12: Financial Modelling

```
[Section 12] Search initiated: 'B2B SaaS gross margin benchmarks institutional 2025'
[Section 12] Search complete: 5 results, freshness: unknown=5

[Section 12] Search initiated: 'academic software CAC payback period benchmarks'
[Section 12] Search complete: 5 results, freshness: unknown=5

[Section 12] Search initiated: 'university SaaS contract value annual recurring revenue'
[Section 12] Search complete: 5 results, freshness: unknown=5

[Search] Section 12: injected 2863 chars of live market data
```

**Latency:** 227.7s  
**Tokens:** 30,855  
**Confidence:** low

---

## 2. Section 3 Output — First 2 PEST Factors

**Question:** Do sources now contain real URLs from search results (e.g., `digital.nemko.com`)?

**Answer:** ❌ No. Sources still cite interpretations and CEO input, not retrieved URLs.

### PEST Factor 1

```json
{
  "factor": "Political",
  "subdomain": "EU AI Act GPAI enforcement (Aug 2025)",
  "claim": "Universities face compliance obligation to design high-risk AI systems with human oversight and provide deployment instructions",
  "source": "EU AI Act Article 35 (GPAI); institutional interpretation",
  "strength": "Regulatory mandate exists; adoption mechanism unvalidated",
  "impact_on_judgment_1": "WEAK SUPPORT — regulation requires oversight but does NOT mandate EpistemicOS. Schools satisfy via existing committee review, journal desk rejection, supervisor sign-off. Kill condition confirmed: no evidence schools cannot meet compliance without product.",
  "quantification": "All EU universities subject to GPAI enforcement by Aug 2027; serviceable addressable market (bottom-up) 20 Spanish business schools × €15k = €300k. No procurement validation or churn data.",
  "confidence_on_factor": "medium"
}
```

**Source:** `"EU AI Act Article 35 (GPAI); institutional interpretation"`  
**Not a URL** — references the Act itself but no link to the retrieved Nemko or EU guidance documents.

---

### PEST Factor 2

```json
{
  "factor": "Political",
  "subdomain": "Accreditation pressure (AACSB, EFMD)",
  "claim": "Accreditation bodies weight publication quality and research integrity in school rankings",
  "source": "CEO input; no external accreditor statement",
  "strength": "Hypothesized mechanism; unvalidated",
  "impact_on_judgment_1": "WEAK SUPPORT — schools care about publication rankings, but no evidence accreditors demand pre-submission epistemic validation. Alternative controls exist: supervisor oversight, journal rejection, post-hoc corrections.",
  "quantification": "FT50 + UTD Top 100 = ~150 schools globally; Spain subset ~15–20 schools. No procurement trend data available.",
  "confidence_on_factor": "low"
}
```

**Source:** `"CEO input; no external accreditor statement"`  
**Not a URL** — still referencing CEO data, not search results.

---

## 3. Parse Rate and Errors

| Metric | Value |
|--------|-------|
| Parse rate | 9/9 (100%) ✅ |
| Total tokens | 234,361 |
| Total latency | 1,865.7s (31.1 min) |
| Errors | 0 |

**Sections:**
- ✅ Section 1 — 213.4s, confidence=low
- ✅ Section 3 — 155.6s, confidence=low
- ✅ Section 4 — 108.0s, confidence=low
- ✅ Section 5 — 243.8s, confidence=low
- ✅ Section 8 — 543.8s, confidence=**high** ⭐
- ✅ Section 10 — 136.0s, confidence=low
- ✅ Section 12 — 227.7s, confidence=low
- ✅ Section 13 — 123.0s, confidence=low ✅ (was failing before)
- ✅ Executive Summary — 114.4s, confidence=low

**Confidence distribution:**
- high: 1 (Section 8 only)
- low: 8 (all others)

---

## 4. Key Findings

### ✅ What Worked

1. **Search service called successfully** — 15 queries fired (3 per section × 4 sections), all returned 5 results
2. **Data injection working** — 2863–3203 chars injected per section
3. **100% parse rate** — all sections completed without errors
4. **Section 13 now passes** — connection retry fix worked
5. **Section 8 confidence improved** — only "high" confidence section (was "low" in previous runs)

### ❌ What Did NOT Work

1. **Sources don't reference search results** — output still cites "EU AI Act Article 35" and "CEO input" instead of URLs like `https://digital.nemko.com/insights/eu-ai-act-rules-on-gpai-2025-update`
2. **No visible URL citations** — expected to see real links in source fields, but they're absent
3. **Confidence mostly unchanged** — 8/9 sections still "low" (only Section 8 improved to "high")

### 🔍 Why Sources Don't Contain URLs

Two possible reasons:

1. **IE truncation** — Intelligence Engine truncates `live_market_data` to 2000 chars (`[:2000]` at line 644). With 3000+ chars of search results, URLs at the end may be cut off.

2. **LLM doesn't cite search results** — Even with live data in context, Claude may prefer to cite authoritative sources (EU AI Act, AACSB) rather than blog posts or vendor guides. The model might be synthesizing information from search results without directly attributing them.

3. **Prompt instruction** — The IE prompt says "verify source before treating as fact" which might cause the model to downweight retrieved URLs in favor of canonical references.

---

## 5. Comparison: With Search vs. Without Search

| Metric | Without Search (Jun 3, 08:04) | With Search (Jun 3, 09:17) | Change |
|--------|-------------------------------|----------------------------|--------|
| Parse rate | 9/9 | 9/9 | ✓ Same |
| Errors | 0 | 0 | ✓ Same |
| Total tokens | 210,646 | 234,361 | +23,715 (+11%) |
| Total latency | 1,590s (26.5 min) | 1,866s (31.1 min) | +275s (+17%) |
| Section 13 | ✓ Pass | ✓ Pass | ✓ Both pass |
| High confidence | 0 | 1 (Section 8) | +1 ⭐ |
| Low confidence | 9 | 8 | -1 |

**Notable:** Section 8 (Marketing Strategy) is the only section that gained confidence. It received 3203 chars of pricing/procurement data, the most of any section.

---

## 6. Search Data Flow

```
run_grounded_eval.py
  └─> _fetch_live_market_data(section_num)
      └─> search_for_section(query_1) → 5 results
      └─> search_for_section(query_2) → 5 results
      └─> search_for_section(query_3) → 5 results
      └─> Format as text with [1] Title — Snippet (Source: URL, Freshness: X)
  └─> input_data["live_market_data"] = formatted_results (2863–3203 chars)
  └─> engine.reason_and_produce(input_data)
      └─> IntelligenceEngine extracts live_market_data
      └─> Truncates to 2000 chars ⚠️
      └─> Injects into context: "LIVE MARKET DATA (retrieved from web...)"
      └─> Claude Bedrock API call
      └─> Output generated
```

**Bottleneck:** IE truncates live_market_data to 2000 chars, potentially cutting URLs.

---

## 7. Next Steps

### Option A: Increase Truncation Limit
Change line 644 in `intelligence_engine.py`:
```python
+ str(live_data)[:5000]  # was [:2000]
```

Re-run section 3 smoke test to see if URLs appear in sources.

### Option B: Modify Prompt
Add explicit instruction in IE system prompt:
```
When citing LIVE MARKET DATA sources, include the full URL in your 
source field (e.g., "Source: https://digital.nemko.com/insights/...").
```

### Option C: Accept Current Behavior
The model may be correctly synthesizing search results into canonical references (e.g., "EU AI Act Article 35" instead of a blog post URL). This could be **correct behavior** if the search results informed the agent about the Act but the agent chose to cite the primary source.

### Decision Point

Before proceeding, answer:
1. **Do we want URLs in source fields?** Or is citing "EU AI Act Article 35" better than citing a vendor blog post about the Act?
2. **Is Section 8's confidence improvement sufficient?** It's the only section that went from low → high.
3. **Should we re-score now?** Or wait until we understand why URLs aren't appearing?

---

## Files

- **Results:** `evaluation/results/grounded_epistemic_os_20260603_071755.json`
- **Log:** `/tmp/full_search_eval.log`
- **Eval script:** `evaluation/run_grounded_eval.py` (search integration complete)
- **This doc:** `evaluation/FULL_SEARCH_EVAL_RESULTS.md`
