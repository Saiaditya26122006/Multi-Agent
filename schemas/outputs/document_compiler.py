from pydantic import BaseModel, Field
from typing import List, Optional


class SectionCompileResult(BaseModel):
    section_number: str
    title: str
    word_count: int = Field(ge=0)
    compiled_successfully: bool = Field(default=True)
    used_fallback: bool = Field(default=False)


class CompiledDocument(BaseModel):
    markdown: str = Field(..., min_length=200, description="Full business plan as Markdown")
    sections_compiled: int = Field(ge=0, description="Number of sections successfully compiled")
    sections_failed: int = Field(default=0, ge=0, description="Sections that fell back to key-value render")
    word_count: int = Field(ge=0, description="Total word count of the document")
    section_results: List[SectionCompileResult] = Field(default_factory=list)
    has_assumptions_appendix: bool = Field(default=False)
    has_quality_appendix: bool = Field(default=False)
    has_council_appendix: bool = Field(default=False)
    assumptions_flagged: int = Field(default=0, ge=0, description="Assumptions needing validation")
