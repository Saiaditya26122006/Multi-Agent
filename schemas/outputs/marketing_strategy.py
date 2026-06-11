from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Competitor(BaseModel):
    name: str
    positioning: str
    pricing: Optional[str] = None
    strengths: List[str]
    weaknesses: List[str]


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class UnitEconomics(BaseModel):
    """Unit economics with LTV, CAC, and key ratios — MAGIC RATIO ENFORCED"""
    cac: dict = Field(..., description="total_cac, breakdown, validation_source, confidence")
    ltv: dict = Field(..., description="calculation_method, avg_revenue_annual, churn_rate, lifetime_years, gross_margin, ltv_gross, ltv_net")
    ltv_cac_ratio: float = Field(..., ge=0)
    payback_period_months: float = Field(..., ge=0)
    health_assessment: str = Field(..., min_length=20)
    magic_ratio_pass: bool = Field(
        ...,
        description="True if LTV:CAC >= 3.0 (or justified exception), False triggers escalation"
    )
    magic_ratio_justification: Optional[str] = Field(
        default=None,
        description="Required if LTV:CAC < 3.0 — explain why this ratio is acceptable for this business model"
    )
    key_assumptions: List[str] = Field(default=[])
    uncertainties: List[str] = Field(default=[])


class MarketingStrategyOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="8")
    target_market_analysis: dict = Field(..., description="segmentation, icp_refined, market_size_tam_sam_som")
    competitors: List[Competitor] = Field(..., min_length=2)
    competitive_advantages: List[str] = Field(..., min_length=2)
    marketing_mix: dict = Field(..., description="product, pricing_policy, distribution, promotion")
    customer_relations: dict = Field(..., description="communication, loyalty_strategy")
    revenue_assumptions: dict = Field(..., description="price_per_unit, volume_year1, volume_year2, volume_year3, sales_cycle_months")
    cac_assumptions: dict = Field(..., description="cac_estimate, cac_source, confidence")
    unit_economics: UnitEconomics = Field(..., description="LTV, CAC, LTV:CAC ratio, payback period")
    market_entry_strategy: str = Field(..., min_length=50)
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
