from pydantic import BaseModel
from typing import Optional, List


class AlliancesInput(BaseModel):
    """Input schema for Alliances & Outsourcing Agent (Section 7)"""
    task_id: str
    session_id: str

    # From Section 1 (Opportunity)
    competitive_strategy: str
    opportunity_description: str

    # From Section 5 (SWOT)
    strategic_implications: Optional[str] = None
    weaknesses: Optional[List[dict]] = None

    # From CEO answers
    partnership_targets: Optional[str] = None
    outsourcing_strategy: Optional[str] = None

    acceptance_criteria: str = ""
