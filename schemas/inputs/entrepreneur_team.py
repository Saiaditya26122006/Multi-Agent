from pydantic import BaseModel
from typing import Optional


class EntrepreneurTeamInput(BaseModel):
    """Input schema for Entrepreneur & Development Team Agent (Section 2)"""
    task_id: str
    session_id: str

    # From Opportunity (Section 1)
    opportunity_description: str
    competitive_strategy: str
    icp_hypothesis: dict

    # From CEO answers (Phase 1 clarification)
    founder_profile: Optional[str] = None
    team_composition: Optional[dict] = None
    founder_background: Optional[str] = None
    relevant_experience: Optional[str] = None

    acceptance_criteria: str = ""
