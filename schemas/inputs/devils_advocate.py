from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class DevilsAdvocateInput(BaseModel):
    task_id: str = Field(..., description="Task ID for tracking")
    session_id: str
    pipeline_run_id: str
    section_number: str = Field(..., description="Which section is being challenged")
    section_output: dict = Field(..., description="The full output from the child agent")
    reasoning_trace: dict = Field(default_factory=dict, description="Reasoning trace from Intelligence Engine")
    cross_section_context: Dict[str, dict] = Field(default_factory=dict, description="Other completed sections for cross-reference")
    acceptance_criteria: str = Field(default="", description="Original acceptance criteria for this section")
