"""Shared data structures for the intelligent-organization runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.knowledge.retriever import RetrievedChunk
from app.rag.pipeline import Citation, RagResult


@dataclass(frozen=True, slots=True)
class Subtask:
    id: str
    question: str
    as_of: date
    label: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "as_of": self.as_of.isoformat(),
            "label": self.label,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Plan:
    strategy: str
    as_of: date
    subtasks: list[Subtask]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "as_of": self.as_of.isoformat(),
            "subtasks": [subtask.to_dict() for subtask in self.subtasks],
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class AgentTrace:
    agent: str
    action: str
    status: str
    detail: str
    latency_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class RetrievalBundle:
    subtask: Subtask
    chunks: list[RetrievedChunk]
    top_score: float
    coverage: float
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask.id,
            "as_of": self.subtask.as_of.isoformat(),
            "chunk_count": len(self.chunks),
            "sources": sorted({chunk.source for chunk in self.chunks}),
            "top_score": self.top_score,
            "coverage": self.coverage,
            "latency_ms": self.latency_ms,
        }


@dataclass(slots=True)
class SubAnswer:
    subtask: Subtask
    result: RagResult
    retrieval: RetrievalBundle

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask": self.subtask.to_dict(),
            "answer": self.result.answer,
            "refused": self.result.refused,
            "citations": len(self.result.citations),
            "latency_ms": self.result.latency_ms,
        }


@dataclass(frozen=True, slots=True)
class AuditReport:
    passed: bool
    quality_score: float
    checks: dict[str, bool]
    findings: list[str]


@dataclass(frozen=True, slots=True)
class ExperienceRecord:
    id: str
    question: str
    normalized_question: str
    as_of: date
    answer: str
    citations: list[Citation]
    retrieved: list[RetrievedChunk]
    quality_score: float
    uses: int
    created_at: str
    last_used_at: str | None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "as_of": self.as_of.isoformat(),
            "answer_preview": self.answer[:240],
            "citation_count": len(self.citations),
            "sources": sorted({citation.source for citation in self.citations}),
            "quality_score": self.quality_score,
            "uses": self.uses,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


@dataclass(slots=True)
class OrganizationResult:
    answer: str
    refused: bool
    security_blocked: bool
    as_of: date
    citations: list[Citation]
    retrieved: list[RetrievedChunk]
    latency_ms: float
    prompt_version: str
    mode: str
    plan_strategy: str
    quality_score: float
    experience_reused: bool
    experience_ids: list[str]
    agents: list[AgentTrace]
    metrics: dict[str, Any] = field(default_factory=dict)
