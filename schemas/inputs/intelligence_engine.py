from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional


class ReasonAndProduceInput(BaseModel):
    agent_role: str = Field(..., description="Description of what the calling agent does")
    input_data: dict = Field(..., description="The input package for this section")
    output_schema_prompt: str = Field(..., description="Prompt describing expected JSON output format")
    cross_section_context: Optional[Dict[str, dict]] = Field(default=None, description="Outputs from other agents")
    reasoning_budget: int = Field(default=3, ge=2, le=4, description="Number of reasoning steps")
    learning_context: str = Field(default="", description="Past failure patterns to avoid")


class GradeEvidenceInput(BaseModel):
    claims: List[dict] = Field(..., description="Assumptions with confidence labels to validate")
    available_evidence: dict = Field(..., description="Evidence available to verify claims against")


class CalibrateConfidenceInput(BaseModel):
    section_output: dict = Field(..., description="The section output to recalibrate")
    devils_advocate_result: dict = Field(..., description="DA verdict and challenges")


class ValidateHypothesesInput(BaseModel):
    section_output: dict = Field(..., description="Section output containing quantitative claims")
    agent_role: str = Field(..., description="Role description for context")


class SoWhatFilterInput(BaseModel):
    section_output: dict = Field(..., description="Section output to evaluate")
    agent_role: str = Field(..., description="Role of the agent that produced it")
