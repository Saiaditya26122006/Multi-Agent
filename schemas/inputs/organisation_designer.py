from pydantic import BaseModel, Field
from typing import List, Optional


class OrganisationDesignerInput(BaseModel):
    task_id: str
    session_id: str
    opportunity_description: str = Field(..., description="From Section 1")
    business_type: str
    founder_profile: Optional[str] = None
    team_composition: Optional[List[dict]] = None
    budget_constraints: Optional[str] = None
    strategic_implications: Optional[str] = Field(None, description="From SWOT if available")
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
