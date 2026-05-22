from pydantic import BaseModel, Field
from typing import List, Optional


class OperationsInput(BaseModel):
    task_id: str
    session_id: str
    opportunity_description: str = Field(..., description="From Section 1")
    business_type: str
    revenue_assumptions: dict = Field(..., description="From Section 8")
    swot_matrix: dict = Field(..., description="From Section 5")
    technology_description: Optional[str] = None
    ip_status: Optional[str] = None
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
