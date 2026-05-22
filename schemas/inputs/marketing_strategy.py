from pydantic import BaseModel, Field
from typing import List, Optional


class MarketingStrategyInput(BaseModel):
    task_id: str
    session_id: str
    swot_matrix: dict = Field(..., description="Full SWOT output from Section 5")
    icp_hypothesis: dict = Field(..., description="From Section 1")
    competitive_strategy: str = Field(..., description="From Section 1")
    market_context: str = Field(..., description="From Section 3")
    strategic_implications: str = Field(..., description="From Section 5")
    pricing_assumption: Optional[str] = None
    target_volume: Optional[str] = None
    cac_assumptions: Optional[str] = None
    partnership_targets: Optional[List[str]] = None
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
