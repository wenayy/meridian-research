"""Typed contracts passed between agents. Every LLM output is validated against one of these."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- planning
class SubQuestion(BaseModel):
    id: str = Field(description="short id like q1, q2")
    question: str
    rationale: str = ""
    search_queries: list[str] = Field(min_length=1, max_length=4)


class ResearchPlan(BaseModel):
    objective: str
    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=8)


class GuardrailVerdict(BaseModel):
    allowed: bool
    reason: str = ""


# ---------------------------------------------------------------- sources & evidence
class Source(BaseModel):
    id: int
    url: str
    title: str
    domain: str
    published: str | None = None
    snippet: str = ""
    text: str = ""
    credibility: float = 0.5
    credibility_reasons: list[str] = Field(default_factory=list)
    found_for: list[str] = Field(default_factory=list)  # sub-question ids


class ExtractedClaim(BaseModel):
    claim: str = Field(description="one self-contained factual statement")
    quote: str = Field(description="verbatim supporting sentence copied from the source")


class Extraction(BaseModel):
    relevant: bool
    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=8)


class Evidence(BaseModel):
    id: int
    source_id: int
    sub_question_id: str
    claim: str
    quote: str
    quote_verified: bool = False  # quote found verbatim (normalised) in the fetched text


# ---------------------------------------------------------------- review
class Conflict(BaseModel):
    topic: str
    positions: list[str] = Field(description="each competing claim, with the evidence ids that back it")
    evidence_ids: list[int] = Field(default_factory=list)


class Review(BaseModel):
    covered: list[str] = Field(default_factory=list, description="sub-question ids answered well")
    gaps: list[SubQuestion] = Field(default_factory=list, max_length=4)
    conflicts: list[Conflict] = Field(default_factory=list)


# ---------------------------------------------------------------- writing
class OutlineSection(BaseModel):
    heading: str
    goal: str
    sub_question_ids: list[str] = Field(default_factory=list)


class Outline(BaseModel):
    title: str
    sections: list[OutlineSection] = Field(min_length=1, max_length=8)


class SectionDraft(BaseModel):
    heading: str
    markdown: str


class Summary(BaseModel):
    executive_summary: str
    key_takeaways: list[str] = Field(min_length=1, max_length=6)


class CitationCheck(BaseModel):
    unsupported: list[str] = Field(default_factory=list, description="sentences whose citations do not support them")


class Report(BaseModel):
    question: str
    title: str
    executive_summary: str
    key_takeaways: list[str]
    sections: list[SectionDraft]
    conflicts: list[Conflict] = Field(default_factory=list)
    sources: list[Source]
    stats: dict = Field(default_factory=dict)

    def markdown(self) -> str:
        from .report import render_markdown

        return render_markdown(self)
