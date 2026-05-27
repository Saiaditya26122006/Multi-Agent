from pydantic import BaseModel, Field
from typing import List


class CompiledDocument(BaseModel):
    markdown: str = Field(..., min_length=200, description="Full business plan as Markdown")
    sections_compiled: int = Field(ge=0, description="Number of sections successfully compiled")
    word_count: int = Field(ge=0, description="Total word count of the document")
    has_assumptions_appendix: bool = Field(default=False)
    has_quality_appendix: bool = Field(default=False)


class SectionCompileResult(BaseModel):
    section_number: str
    title: str
    word_count: int = Field(ge=0)
    compiled_successfully: bool = Field(default=True)
