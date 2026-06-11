# System Improvements Complete — 2026-06-11

All 3 proposed improvements have been successfully implemented.

---

## ✅ Improvement 1: TAM-SAM-SOM Framework (Opportunity Analyst)

**Problem**: Vague "market size: $X billion" outputs without structure or capture rate logic.

**Solution**: Enforced investor-standard TAM-SAM-SOM framework.

### Changes Made

**Schema** (`schemas/outputs/opportunity_analyst.py`):
- Added `MarketSizing` model with strict structure:
  - `tam`, `tam_definition`, `tam_source` (must cite market research)
  - `sam`, `sam_definition`, `sam_calculation` (must show filtering math)
  - `som_year_1`, `som_year_3`, `som_logic` (must explain constraints)
  - `capture_rate_year_1_pct`, `capture_rate_year_3_pct` (calculated automatically)
- Added validator: `capture_rate_year_1_pct > 5%` → rejected (unrealistic for startups)

**Agent** (`agents/phase2/opportunity_analyst.py`):
- Updated SYSTEM_PROMPT with TAM-SAM-SOM reasoning framework
- TAM must cite source (market research URL, industry report, or explicit calculation)
- SAM must show filtering logic: `TAM × geography × segment × budget`
- SOM must explain constraints: sales capacity, brand awareness, competition
- Capture rate Year 1 typically 0.1-5% for startups

### Example Output

```json
{
  "market_sizing": {
    "tam": 1000000000,
    "tam_definition": "Global software market for research and academic institutions",
    "tam_source": "Gartner 2025 Global Software Market Report, page 42",
    "sam": 50000000,
    "sam_definition": "Academic software in Europe for research validation",
    "sam_calculation": "TAM (1B) × 5% (Europe) × 10% (academic) = 50M",
    "som_year_1": 250000,
    "som_year_3": 2500000,
    "som_logic": "Sales capacity of 2 reps × 10 deals/month × $2K ACV × 50% close rate",
    "capture_rate_year_1_pct": 0.5,
    "capture_rate_year_3_pct": 5.0
  }
}
```

### Testing

```bash
python3 -c "from schemas.outputs.opportunity_analyst import MarketSizing; ..."
# ✅ Validates structure
# ✅ Rejects >5% Year 1 capture rate
# ✅ Enforces min_length on definitions
```

---

## ✅ Improvement 2: Magic Ratio Guardrail (Marketing Strategy)

**Problem**: Marketing agent could output LTV:CAC < 3:1 without justification, allowing fundamentally broken unit economics to proceed.

**Solution**: Hard-coded guardrail that **blocks agent from proceeding** if LTV:CAC < 3:1 without valid justification.

### Changes Made

**Schema** (`schemas/outputs/marketing_strategy.py`):
- Added `magic_ratio_pass: bool` to `UnitEconomics` model
- Added `magic_ratio_justification: Optional[str]` for exceptions
- Agent must set `magic_ratio_pass = True` if ratio ≥ 3.0 OR if valid exception exists

**Agent** (`agents/phase2/marketing_strategy.py`):
- Updated SYSTEM_PROMPT with Magic Ratio Guardrail rules
- Added enforcement logic after output validation:
  ```python
  if ltv_cac_ratio < 3.0 and not magic_ratio_pass:
      # HARD STOP — escalate to CEO
      await self._escalate(
          trigger="unit_economics_failure",
          notes="LTV:CAC ratio is X.X (below 3:1 threshold). Options: ..."
      )
      return  # Do NOT proceed to Council
  ```
- Acceptable exceptions (must provide justification):
  - **Marketplace/Platform**: Network effects improve over time
  - **Land-and-expand**: Enterprise SaaS with high expansion revenue
  - **VC-funded land grab**: Intentional negative unit economics for market capture
  - **E-commerce repeat purchases**: Blended LTV:CAC improves by Year 2

### Example Outputs

**Valid (ratio ≥ 3.0)**:
```json
{
  "unit_economics": {
    "ltv_cac_ratio": 4.2,
    "magic_ratio_pass": true,
    "magic_ratio_justification": null
  }
}
```

**Exception Granted (ratio < 3.0 WITH justification)**:
```json
{
  "unit_economics": {
    "ltv_cac_ratio": 2.4,
    "magic_ratio_pass": true,
    "magic_ratio_justification": "Land-and-expand: 70% of customers expand to 4x ACV by Year 2 (validated by Gong S-1)"
  }
}
```

**Blocked (ratio < 3.0 WITHOUT justification)**:
```json
{
  "unit_economics": {
    "ltv_cac_ratio": 2.4,
    "magic_ratio_pass": false,
    "magic_ratio_justification": null
  }
}
```
→ Agent escalates to CEO immediately, does NOT proceed to Council

### CEO Interaction

When escalation occurs:
```
🚨 An agent needs clarification before continuing:

LTV:CAC ratio is 2.4 (below 3:1 threshold).

Options:
(1) Increase pricing → raises LTV
(2) Reduce CAC via cheaper channels
(3) Increase retention (lower churn) → extends customer lifetime
(4) Provide valid justification (marketplace network effects, land-and-expand, VC-funded land grab)

Reply with your decision or 'agent' to delegate.
```

### Testing

```bash
python3 -c "from schemas.outputs.marketing_strategy import UnitEconomics; ..."
# ✅ Accepts ratio ≥ 3.0
# ✅ Accepts ratio < 3.0 WITH justification
# ✅ Schema allows magic_ratio_pass=False (agent will escalate)
```

---

## ✅ Improvement 3: Cost & Headcount Schema Validation

**Problem**: Operations (cost_structure) and HR Plan (headcount_plan) used loose `dict` types, allowing Financial model to receive inconsistent data.

**Solution**: Replaced `dict` with strict Pydantic models with required fields.

### Changes Made

**Operations Schema** (`schemas/outputs/operations.py`):
```python
class CostStructure(BaseModel):
    fixed_costs_monthly: float = Field(..., ge=0)
    cogs_per_unit: float = Field(..., ge=0)
    variable_costs_per_unit: float = Field(default=0, ge=0)
    initial_cash: float = Field(..., gt=0)
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    confidence: Literal["high", "medium", "low"]
```

**HR Plan Schema** (`schemas/outputs/hr_plan.py`):
```python
class MonthlyHeadcount(BaseModel):
    headcount: int = Field(..., ge=0)
    total_cost_monthly: float = Field(..., ge=0)
    roles: List[str]

class HRPlanOutput(BaseModel):
    headcount_plan: Dict[str, MonthlyHeadcount] = Field(...)

    @field_validator('headcount_plan')
    @classmethod
    def validate_headcount_structure(cls, v):
        required_months = ["month_0", "month_6", "month_12"]
        for month in required_months:
            if month not in v:
                raise ValueError(f"Missing required key '{month}'")
        return v
```

### Benefits

1. **Type Safety**: Financial model receives validated structure, not arbitrary dicts
2. **Required Fields**: Missing `fixed_costs_monthly` or `month_12` → validation error
3. **Non-negative Constraints**: Cannot have negative costs or headcount
4. **Explicit Confidence**: Every cost assumption must state confidence level

### Testing

```bash
python3 -c "from schemas.outputs.operations import CostStructure; ..."
# ✅ Validates all required fields
# ✅ Enforces ge=0 on costs
# ✅ Requires source and confidence

python3 -c "from schemas.outputs.hr_plan import MonthlyHeadcount; ..."
# ✅ Validates month_0, month_6, month_12 required
# ✅ Enforces structure per month
```

---

## Summary Table

| Improvement | Status | Priority | Files Modified | Impact |
|-------------|--------|----------|----------------|--------|
| **1. TAM-SAM-SOM** | ✅ Complete | P1 HIGH | 2 | Forces structured market sizing with sources |
| **2. Magic Ratio** | ✅ Complete | P0 CRITICAL | 2 | Prevents broken unit economics from proceeding |
| **3. Schema Validation** | ✅ Complete | P2 MEDIUM | 2 | Ensures Financial model receives valid inputs |

**Total Files Modified**: 6  
**Total Lines Added**: ~400  
**Implementation Date**: 2026-06-11

---

## Files Modified

```
schemas/outputs/opportunity_analyst.py    — Added MarketSizing model + validator
agents/phase2/opportunity_analyst.py      — Updated SYSTEM_PROMPT with TAM-SAM-SOM framework

schemas/outputs/marketing_strategy.py     — Added magic_ratio_pass + justification fields
agents/phase2/marketing_strategy.py       — Added Magic Ratio enforcement logic

schemas/outputs/operations.py             — Replaced dict with CostStructure model
schemas/outputs/hr_plan.py                — Replaced dict with MonthlyHeadcount model + validator
```

---

## Testing Checklist

### TAM-SAM-SOM Testing
- [ ] Run Opportunity Analyst with sample business idea
- [ ] Verify market_sizing output contains all required fields
- [ ] Test capture_rate_year_1_pct > 5% → rejected
- [ ] Verify tam_source and sam_calculation are populated

### Magic Ratio Testing
- [ ] Force Marketing to output LTV:CAC = 2.5 without justification
- [ ] Verify agent escalates to CEO (does NOT reach Council)
- [ ] Test with valid justification → should proceed
- [ ] Test with LTV:CAC ≥ 3.0 → should proceed normally

### Schema Validation Testing
- [ ] Run Operations agent → verify cost_structure is CostStructure model
- [ ] Run HR Plan agent → verify headcount_plan has month_0/6/12
- [ ] Force missing month_12 → should fail validation
- [ ] Verify Financial model receives structured inputs

---

## Integration Notes

### Dependency Chain
1. **Opportunity Analyst** outputs `market_sizing` → feeds Marketing Strategy
2. **Marketing Strategy** enforces Magic Ratio → feeds Financial Model
3. **Operations** outputs `CostStructure` → feeds Financial Model
4. **HR Plan** outputs `headcount_plan` (validated structure) → feeds Financial Model
5. **Financial Model** consumes validated inputs → runs SimPy with clean data

### Backward Compatibility
- **⚠️ BREAKING CHANGE**: Operations and HR Plan outputs changed from `dict` to Pydantic models
- **Impact**: Any code expecting raw dicts must be updated to access model fields
- **Migration**: Use `.model_dump()` to get dict representation if needed

### Next Steps (Recommended)
1. **Update Financial Model** to expect `CostStructure` and `MonthlyHeadcount` types
2. **Add unit tests** for all 3 improvements
3. **Run full pipeline** with sample business idea end-to-end
4. **Document Magic Ratio exceptions** in gap_resolution_rules.yaml
5. **Add TAM-SAM-SOM examples** to agent training data

---

## Status: 🟢 PRODUCTION-READY (after integration testing)

All 3 improvements implemented and validated. System now has:
- ✅ Structured market sizing with capture rate validation
- ✅ Unit economics guardrail preventing broken business models
- ✅ Type-safe cost and headcount schemas

**Recommended**: Run integration testing before deploying to production.
