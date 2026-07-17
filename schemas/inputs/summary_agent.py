from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class SummaryAgentInput(BaseModel):
    task_id: str
    session_id: str
    pipeline_run_id: str = Field(default="")
    completed_sections: Dict[str, Any] = Field(
        ...,
        description="Dict of section_number -> section output. Only completed sections included.",
    )
    flagged_assumptions: List[dict] = Field(
        default=[],
        description="Assumptions across all sections that are labelled assumed or low confidence",
    )
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
