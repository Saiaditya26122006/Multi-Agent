# Discounted Cash Flow (DCF) Model Skill

## Purpose
Estimate the intrinsic value of the startup based on projected future free cash flows.

## When to Apply
- Only when Year 1 revenue assumptions have some validation (not purely assumed)
- Skip if all revenue assumptions are labelled "assumed" with no evidence
- Flag as "indicative only" for pre-revenue businesses

## Methodology

### Step 1: Project Free Cash Flows (FCF)
- FCF = EBITDA - Capex - Changes in Working Capital - Taxes
- Use Years 1-3 from three_statement_model
- Extrapolate Years 4-5 using growth rate decay:
  - Year 4 growth = Year 3 growth × 0.7
  - Year 5 growth = Year 4 growth × 0.7

### Step 2: Terminal Value
- Use perpetuity growth method:
  - Terminal Value = FCF_Year5 × (1 + g) / (WACC - g)
  - g (terminal growth rate) = 2-3% (GDP growth proxy)
- Alternative: exit multiple method if comparable exits exist
  - Terminal Value = Year 5 Revenue × exit_multiple

### Step 3: Discount Rate (WACC)
- For early-stage startups, use risk-adjusted rates:
  - Pre-revenue: 40-60%
  - Early revenue (<$1M ARR): 30-40%
  - Growth stage (>$1M ARR): 20-30%
- If debt exists: WACC = (E/V × Re) + (D/V × Rd × (1-T))
- For most Phase 2 startups: assume 100% equity, use Re directly

### Step 4: Calculate Present Value
- PV of each year's FCF = FCF_n / (1 + WACC)^n
- PV of Terminal Value = TV / (1 + WACC)^5
- Enterprise Value = Sum of PV(FCFs) + PV(TV)

### Step 5: Sensitivity Analysis
- Run 3 scenarios varying:
  - Revenue growth rate: ±20%
  - Discount rate: ±5 percentage points
  - Terminal growth rate: 1% to 4%
- Present as sensitivity table

## Output Format
```json
{
  "enterprise_value_base": float,
  "enterprise_value_optimistic": float,
  "enterprise_value_pessimistic": float,
  "discount_rate_used": float,
  "terminal_growth_rate": float,
  "terminal_value_pct_of_total": float,
  "methodology_notes": str,
  "confidence_flag": "indicative" | "directional" | "robust"
}
```

## Caveats to Include in Output
- Terminal value typically represents 60-80% of total value — flag if higher
- DCF is highly sensitive to discount rate — always show sensitivity
- Pre-revenue DCF is inherently speculative — label confidence accordingly
- Never present a single point estimate without the range
