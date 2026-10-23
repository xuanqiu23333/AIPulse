"""Pydantic boundaries for research requests, evidence, graph state and reports."""

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aipulse.llm.gateway import TokenUsage
from aipulse.llm.validation import SchemaDiagnostic
from aipulse.news.summary import _valid_stored_url


MAX_ROUNDS = 3  # Initial search plus at most two supplementary searches.
MAX_SUPPLEMENTS = 2
MAX_MODEL_CALLS = 2
MAX_EVIDENCE = 18
RESULTS_PER_SOURCE = 3
TASK_TIMEOUT_SECONDS = 360
PLAN_MAX_TOKENS = 1000
REPORT_MAX_TOKENS = 6144
MAX_OUTPUT_TOKENS = PLAN_MAX_TOKENS + REPORT_MAX_TOKENS
PROMPT_VERSION = "research-zh-v6"
RESEARCH_REVISION = "20261021_0004"
TaskStatus = Literal["queued", "running", "completed", "partial", "failed", "interrupted"]
SourceType = Literal["stored_news", "arxiv", "github", "hn"]
Coverage = Literal["title_only", "metadata", "excerpt", "abstract"]
_URL = re.compile(r"https?://|www\.", re.I)


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, validate_assignment=True)


def plain_request(value: str) -> str:
    value = value.strip()
    if not value or _URL.search(value) or any(ord(char) < 32 for char in value):
        raise ValueError("Use a plain research topic without URLs or control characters")
    return value


class ResearchRequest(Boundary):
    topic: str = Field(min_length=3, max_length=300, strict=True)
    focus: str | None = Field(default=None, min_length=2, max_length=600, strict=True)
    event_id: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("topic", "focus")
    @classmethod
    def safe_request(cls, value: str | None) -> str | None:
        return None if value is None else plain_request(value)


class PlannedQuestion(Boundary):
    question: str = Field(min_length=3, max_length=200)
    search_query: str = Field(min_length=2, max_length=160)

    @field_validator("question", "search_query")
    @classmethod
    def no_tool_targets(cls, value: str) -> str:
        return plain_request(value)


class PlanDraft(Boundary):
    questions: list[PlannedQuestion] = Field(min_length=1, max_length=3)


class ResearchQuestion(PlannedQuestion):
    question_id: str = Field(pattern=r"^Q[1-3]$")


class EvidenceCandidate(Boundary):
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=191)
    source_url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(default="", max_length=2000)
    coverage: Coverage
    quality_note: str = Field(max_length=300)
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    identity_url: str | None = Field(default=None, max_length=2048)

    @field_validator("source_url", "identity_url")
    @classmethod
    def public_url(cls, value: str | None) -> str | None:
        if value is not None and not _valid_stored_url(value):
            raise ValueError("A public HTTP source URL is required")
        return value


class CoverageSupport(Boundary):
    question_id: str = Field(pattern=r"^Q[1-3]$")
    quote: str = Field(min_length=12, max_length=350)
    capabilities: list[str] = Field(min_length=1, max_length=5)


class ResearchEvidence(EvidenceCandidate):
    citation: str = Field(pattern=r"^E[0-9]{3}$")
    relevance_note: str = Field(max_length=300)
    question_ids: list[str] = Field(max_length=3)
    coverage_supports: list[CoverageSupport] = Field(default_factory=list, max_length=9)


class SupportQuote(Boundary):
    citation: str = Field(pattern=r"^E[0-9]{3}$")
    quote: str = Field(min_length=12, max_length=350, description="Exact source excerpt, not a paraphrase.")


class ClaimDraft(Boundary):
    text_zh: str = Field(min_length=8, max_length=500)
    supports: list[SupportQuote] = Field(min_length=1, max_length=3)

    @field_validator("text_zh")
    @classmethod
    def source_links_are_server_owned(cls, value: str) -> str:
        if _URL.search(value) or not value.strip():
            raise ValueError("Generated claims must not contain URLs")
        return value.strip()


class AnalysisDraft(Boundary):
    question_id: str = Field(pattern=r"^Q[1-3]$")
    claims: list[ClaimDraft] = Field(max_length=3)


class ReportDraft(Boundary):
    """Provider-owned draft only; status, sources and metrics are server-owned."""
    overview: list[ClaimDraft] = Field(default_factory=list, max_length=2)
    conclusions: list[ClaimDraft] = Field(max_length=5)
    analyses: list[AnalysisDraft] = Field(default_factory=list, max_length=3)
    trends: list[ClaimDraft] = Field(default_factory=list, max_length=2)
    limitations: list[str] = Field(default_factory=list, max_length=5,
                                   description="Non-empty strings, at most 300 characters each, no URLs.")

    @field_validator("limitations")
    @classmethod
    def bounded_limitations(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 300 or _URL.search(value) for value in values):
            raise ValueError("Invalid limitation text")
        return [value.strip() for value in values]


class VerifiedClaim(Boundary):
    text_zh: str
    citations: list[str]
    support_quotes: list[SupportQuote]
    verification: Literal["source_attributed"] = "source_attributed"


class ClaimValidationIssue(Boundary):
    """Bounded parsed candidate diagnostics, never a raw provider response."""

    section: Literal["overview", "conclusions", "analyses", "trends"]
    index: int = Field(ge=0, le=9)
    question_id: str | None = Field(default=None, pattern=r"^Q[1-3]$")
    text_zh: str = Field(max_length=500)
    supports: list[SupportQuote] = Field(max_length=3)
    reasons: list[Literal[
        "unknown_evidence", "title_only", "quote_not_found", "number_not_supported",
        "unknown_question", "duplicate_question",
    ]] = Field(min_length=1, max_length=6)


class AnalysisSection(Boundary):
    question_id: str
    question: str
    claims: list[VerifiedClaim]


class ReportSource(Boundary):
    citation: str
    source_type: SourceType
    source_url: str
    title: str
    coverage: Coverage
    quality_note: str
    excerpt: str


class RunMetrics(Boundary):
    model_calls: int = Field(default=0, ge=0, le=MAX_MODEL_CALLS)
    search_rounds: int = Field(default=0, ge=0, le=MAX_ROUNDS)
    supplementary_rounds: int = Field(default=0, ge=0, le=MAX_SUPPLEMENTS)
    token_usage: TokenUsage | None = None
    usage_missing_calls: int = Field(default=0, ge=0, le=MAX_MODEL_CALLS)
    model_latency_ms: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class ResearchReport(Boundary):
    topic: str
    status: Literal["completed", "partial", "failed"]
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    overview: list[VerifiedClaim] = Field(default_factory=list)
    conclusions: list[VerifiedClaim] = Field(default_factory=list)
    analyses: list[AnalysisSection] = Field(default_factory=list)
    trends: list[VerifiedClaim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    source_warnings: list[str] = Field(default_factory=list, max_length=20)
    validation_issues: list[ClaimValidationIssue] = Field(default_factory=list, max_length=20)
    schema_diagnostics: list[SchemaDiagnostic] = Field(default_factory=list, max_length=3)
    prompt_version: str = PROMPT_VERSION


class TaskPayload(Boundary):
    schema_version: Literal[1] = 1
    request: ResearchRequest
    stage: str = "queued"
    report: ResearchReport | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    error_code: str | None = None
    stop_reason: str | None = None
    evidence_count: int = Field(default=0, ge=0, le=MAX_EVIDENCE)


class ProgressEvent(Boundary):
    seq: int
    node_name: str
    event_type: str
    payload: dict
    created_at: datetime


class TaskSnapshot(Boundary):
    task_id: str
    request: ResearchRequest
    status: TaskStatus
    stage: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    metrics: RunMetrics
    evidence_count: int
    report_ready: bool
    error_code: str | None
    stop_reason: str | None
    events: list[ProgressEvent] = Field(default_factory=list)


class ResearchState(Boundary):
    request: ResearchRequest
    questions: list[ResearchQuestion] = Field(default_factory=list, max_length=3)
    current_query: str = ""
    used_queries: list[str] = Field(default_factory=list, max_length=MAX_ROUNDS)
    rounds: int = Field(default=0, ge=0, le=MAX_ROUNDS)
    supplements: int = Field(default=0, ge=0, le=MAX_SUPPLEMENTS)
    candidates: list[EvidenceCandidate] = Field(default_factory=list, max_length=12)
    evidence: list[ResearchEvidence] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    gaps: list[str] = Field(default_factory=list, max_length=3)
    errors: list[str] = Field(default_factory=list, max_length=20)
    schema_diagnostics: list[SchemaDiagnostic] = Field(default_factory=list, max_length=3)
    draft: ReportDraft | None = None
    report: ResearchReport | None = None
    stop_reason: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
