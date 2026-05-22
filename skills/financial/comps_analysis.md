# Comparable Company Analysis Skill

## Purpose
Benchmark the startup's projected metrics against comparable companies to validate assumptions and provide relative valuation context.

## When to Apply
- Always attempt if business_type and market are known
- Skip only if the business is so novel that no comparable exists (rare)
- Label confidence based on how close the comparables actually are

## Methodology

### Step 1: Identify Comparable Companies
- Match on: industry, business model (B2B/B2C/marketplace), stage, geography
- Ideal: 3-5 companies at similar stage or that were at similar stage historically
- Acceptable: public companies in same space (use their early-stage metrics if available)
- Sources to reference: Crunchbase, PitchBook benchmarks, industry reports

### Step 2: Select Metrics to Compare
For SaaS/subscription businesses:
- ARR growth rate (Year 1-2)
- Net Revenue Retention (NRR)
- CAC payback period (months)
- LTV:CAC ratio
- Gross margin
- Burn multiple (net burn / net new ARR)

For marketplace/transactional businesses:
- GMV growth rate
- Take rate
- Unit economics (contribution margin per transaction)
- Repeat purchase rate

For product/hardware businesses:
- Gross margin
- Revenue per employee
- Inventory turnover
- Time to market

### Step 3: Derive Valuation Multiples
- Revenue multiple: EV / Revenue (use forward revenue for growth companies)
- Typical ranges by stage:
  - Pre-revenue: N/A (use milestone-based)
  - <$1M ARR: 10-30x (highly variable)
  - $1-5M ARR: 8-20x
  - $5-20M ARR: 6-15x
- Adjust for growth rate: higher growth = higher multiple

### Step 4: Apply to Target
- Implied valuation = Target's projected metric × comparable multiple
- Show range: low/mid/high based on comparable spread
- Cross-check: does implied valuation make sense given stage and market?

## Output Format
```json
{
  "comparables": [
    {
      "name": str,
      "relevance": "high" | "medium" | "low",
      "metrics": {"arr_growth": float, "gross_margin": float, ...},
      "valuation_multiple": float,
      "source": str
    }
  ],
  "implied_valuation_range": {"low": float, "mid": float, "high": float},
  "key_metric_benchmarks": {
    "metric_name": {"target_value": float, "benchmark_median": float, "percentile": str}
  },
  "methodology_notes": str
}
```

## Rules
- Never fabricate comparable company data — if uncertain, label as "estimated from industry benchmarks"
- Always note the vintage of comparable data (metrics from 2020 are less relevant in 2026)
- If fewer than 3 comparables found, flag as "insufficient comparables — treat as directional only"
- Separate operational comparables (for assumption validation) from valuation comparables
