from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class CouncilReviewInput(BaseModel):
    section_number: str = Field(..., description="Section number being reviewed")
    section_name: str = Field(default="", description="Human-readable section name")
    agent_name: str = Field(..., description="Name of the agent that produced this output")
    output: dict = Field(..., description="The child agent's full output JSON")
    cross_section_context: Optional[Dict[str, dict]] = Field(
        default=None, description="Prior section outputs for cross-reference"
    )
    task_id: str = Field(default="")
    session_id: str = Field(default="")
    pipeline_run_id: str = Field(default="")
    attempt: int = Field(default=1, ge=1, le=3, description="Which review attempt this is")
    is_revision: bool = Field(default=False, description="Whether this is a revised output")
