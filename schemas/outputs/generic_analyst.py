"""Output schema for the Generic Analyst agent (Build v2 custom sections).

Deliberately flexible: a narrative plus key points and honest
assumptions/uncertainties — enough structure to render, ground, and critique
like a built-in section, without locking the agent to one topic's fields.
``extra='allow'`` preserves the task_id/model_used/section_number fields the
base agent stamps on after validation.
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class GenericAnalystOutput(BaseModel):
    """A custom section's analysis."""

    model_config = ConfigDict(extra="allow")

    section_number: str = Field("custom")
    title: str = Field(..., min_length=1)
    narrative: str = Field(..., min_length=100)
    key_points: List[str] = Field(default_factory=list)
    assumptions_used: List[str] = Field(default_factory=list)
    uncertainties: List[str] = Field(default_factory=list)
    confidence_score: str = Field("low")
    input_tokens: int = 0
    output_tokens: int = 0
