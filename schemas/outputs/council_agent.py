from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class PersonaCritique(BaseModel):
    persona: Literal["skeptic", "architect", "visionary", "stranger", "operator"]
    top_finding: str = Field(..., description="Main finding from this persona")
    severity: Literal["critical", "minor", "none"] = Field(...)
    detail: str = Field(default="", description="Elaboration on the finding")


class CouncilVerdict(BaseModel):
    decision: Literal["pass", "revise", "escalate"] = Field(...)
    score: float = Field(ge=0.0, le=10.0)
    critical_count: int = Field(default=0, ge=0)
    minor_count: int = Field(default=0, ge=0)
    feedback: str = Field(default="", description="Combined revision instructions")
    improvements: List[str] = Field(default_factory=list)


class CouncilReport(BaseModel):
    section_number: str
    agent_name: str
    attempt: int = Field(default=1, ge=1)
    score: float = Field(ge=0.0, le=10.0)
    decision: Literal["pass", "revise", "escalate"]
    critiques: List[PersonaCritique] = Field(default_factory=list)
    improvements_made: List[str] = Field(default_factory=list)
    revision_instructions: Optional[str] = Field(default=None)


class FullCouncilSummary(BaseModel):
    session_id: str
    pipeline_run_id: str
    sections_reviewed: List[CouncilReport] = Field(default_factory=list)
    overall_quality_score: float = Field(ge=0.0, le=10.0)
    total_revisions_triggered: int = Field(default=0, ge=0)
    strongest_section: str = Field(default="")
    weakest_section: str = Field(default="")
