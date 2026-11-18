"""Pydantic contracts shared by the HTTP API and services."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)
    source: str | None = Field(default=None, max_length=300)
    content_type: str = Field(default="text/markdown", max_length=100)
    policy_key: str | None = Field(default=None, max_length=80)
    version: str | None = Field(default=None, max_length=40)
    effective_from: date | None = None
    effective_until: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("source")
    @classmethod
    def _clean_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().replace("\\", "/").lstrip("/")
        if not value:
            return None
        if ".." in value.split("/"):
            raise ValueError("source must not contain parent-directory segments")
        return value

    @field_validator("policy_key", "version")
    @classmethod
    def _clean_version_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validate_policy_window(self) -> DocumentCreate:
        if self.policy_key and self.effective_from is None:
            raise ValueError("policy_key requires effective_from")
        if self.version and not self.policy_key:
            raise ValueError("version requires policy_key")
        if self.effective_until and self.effective_from is None:
            raise ValueError("effective_until requires effective_from")
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until must be on or after effective_from")
        return self


class DocumentSummary(BaseModel):
    id: str
    title: str
    source: str
    content_type: str
    checksum: str
    fingerprint: str | None = None
    policy_key: str | None = None
    version: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    status: str
    chunk_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class DocumentDetail(DocumentSummary):
    content: str


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class IngestResult(BaseModel):
    ingested: int
    updated: int
    skipped: int
    documents: list[DocumentSummary]
    errors: list[str] = Field(default_factory=list)


class DocumentVersionOut(BaseModel):
    id: str
    document_id: str
    version: str | None = None
    policy_key: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    checksum: str
    fingerprint: str
    content: str
    created_at: str


class DocumentVersionList(BaseModel):
    items: list[DocumentVersionOut]
    total: int


class ChatRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=10)
    document_ids: list[str] | None = Field(default=None, max_length=20)
    as_of: date | None = Field(
        default=None,
        description="Answer with the policy versions effective on this date.",
    )

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value

    @field_validator("document_ids")
    @classmethod
    def _deduplicate_document_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value)) or None


class RetrievedChunkOut(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    source: str
    ordinal: int
    section: str | None = None
    policy_key: str | None = None
    version: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    content: str
    score: float
    vector_score: float
    lexical_score: float


class CitationOut(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    title: str
    source: str
    section: str | None = None
    policy_key: str | None = None
    version: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    quote: str | None = None
    quote_verified: bool = False
    snippet: str
    score: float


class AgentTraceOut(BaseModel):
    agent: str
    action: str
    status: str
    detail: str
    latency_ms: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    run_id: str
    question: str
    answer: str
    refused: bool
    security_blocked: bool
    as_of: date
    citations: list[CitationOut]
    retrieved_chunks: list[RetrievedChunkOut]
    latency_ms: float
    prompt_version: str
    mode: str
    created_at: str
    plan_strategy: str = "single_question"
    quality_score: float = 0.0
    experience_reused: bool = False
    experience_ids: list[str] = Field(default_factory=list)
    agents: list[AgentTraceOut] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    question: str
    answer_preview: str
    refused: bool
    security_blocked: bool
    status: str
    mode: str
    citation_count: int
    latency_ms: float
    created_at: str


class RunDetail(RunSummary):
    answer: str
    retrieval: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    metrics: dict[str, Any]
    prompt_version: str
    error: str | None = None


class RunList(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    version: str
    environment: str
    database: Literal["ok", "error"]
    documents: int
    chunks: int
    runs: int
    generator: str
    embedding: str


class EvalRunRequest(StrictModel):
    name: str = Field(default="manual", min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    top_k: int | None = Field(default=None, ge=1, le=10)
    write_artifact: bool = True


class EvalRunSummary(BaseModel):
    run_id: str
    name: str
    dataset_name: str
    status: str
    total_cases: int
    pass_rate: float
    metrics: dict[str, Any]
    created_at: str
    finished_at: str | None = None


class EvalRunList(BaseModel):
    items: list[EvalRunSummary]
    total: int


class DatasetPreview(BaseModel):
    name: str
    path: str
    checksum: str
    case_count: int
    categories: dict[str, int]
    cases: list[dict[str, Any]]


class AgentProfile(BaseModel):
    name: str
    responsibility: str


class OrganizationStatus(BaseModel):
    name: str
    principle: str
    agents: list[AgentProfile]
    experience_total: int
    experience_uses: int
    average_quality: float
    experience_reuse_enabled: bool
    loop: list[str]


class ExperienceOut(BaseModel):
    id: str
    question: str
    as_of: date
    answer_preview: str
    citation_count: int
    sources: list[str]
    quality_score: float
    uses: int
    created_at: str
    last_used_at: str | None = None


class ExperienceList(BaseModel):
    items: list[ExperienceOut]
    total: int
    limit: int
    offset: int


class FeedbackRequest(StrictModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)
    corrected_answer: str | None = Field(default=None, max_length=5000)


class FeedbackResponse(BaseModel):
    feedback_id: str
    run_id: str
    rating: int
    action: str
    experience_id: str | None = None


class EvolutionRequest(StrictModel):
    apply: bool = False


class EvolutionCandidate(BaseModel):
    min_query_coverage: float
    pass_rate: float
    retrieval_hit_rate: float
    citation_precision: float
    injection_resistance: float
    p95_latency_ms: float


class EvolutionReport(BaseModel):
    baseline: EvolutionCandidate
    candidates: list[EvolutionCandidate]
    best: EvolutionCandidate
    recommended: bool
    applied: bool
    applied_value: float | None = None
    duration_ms: float
    caveat: str
