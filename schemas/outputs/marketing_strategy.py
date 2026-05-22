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
    market_entry_strategy: str = Field(..., min_length=50)
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
