from pydantic import BaseModel, Field
from typing import List, Optional


class SWOTSynthesizerInput(BaseModel):
    task_id: str
    session_id: str
    pest_analysis: List[dict] = Field(..., description="From Section 3")
    five_forces: List[dict] = Field(..., description="From Section 3")
    risks_opportunities: dict = Field(..., description="From Section 3")
    capability_gaps: List[dict] = Field(..., description="From Section 4")
    org_structure: str = Field(..., description="From Section 4")
    opportunity_description: str = Field(..., description="From Section 1")
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
