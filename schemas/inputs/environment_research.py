from pydantic import BaseModel, Field
from typing import List, Optional


class EnvironmentResearchInput(BaseModel):
    task_id: str
    session_id: str
    market_scope: str = Field(..., description="Geographic and segment scope from Section 1")
    business_type: str = Field(..., description="Type of business being analysed")
    icp_hypothesis: dict = Field(..., description="From Section 1 output")
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
