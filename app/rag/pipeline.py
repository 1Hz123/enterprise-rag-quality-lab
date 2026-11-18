"""End-to-end RAG pipeline with guardrails and citation validation."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.config import Settings
from app.knowledge.embeddings import lexical_tokens
from app.knowledge.retriever import RetrievedChunk, Retriever
from app.rag.generator import Generation, Generator
from app.rag.prompt import (
    INJECTION_ANSWER,
    NO_CONTEXT_ANSWER,
    PROMPT_VERSION,
    UNSAFE_CONTEXT_ANSWER,
    build_context_items,
    looks_like_prompt_injection,
)
from app.rag.query import normalize_query

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


@dataclass(frozen=True, slots=True)
class Citation:
    index: int
    chunk_id: str
    document_id: str
    title: str
    source: str
    section: str | None
    policy_key: str | None
    version: str | None
    effective_from: str | None
    effective_until: str | None
    snippet: str
    score: float
    quote: str | None = None
    quote_verified: bool = False


@dataclass(slots=True)
class RagResult:
    answer: str
    refused: bool
    security_blocked: bool
    as_of: date
    citations: list[Citation]
    retrieved: list[RetrievedChunk]
    latency_ms: float
    prompt_version: str
    mode: str
    metrics: dict[str, Any] = field(default_factory=dict)
    raw_answer: str = ""
    invalid_citation_count: int = 0
    invalid_citation_rate: float = 0.0
    fallback_used: bool = False


class RAGPipeline:
    def __init__(
        self,
        retriever: Retriever,
        generator: Generator,
        settings: Settings,
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.settings = settings

    def _prepare_question(self, question: str) -> str:
        normalized = normalize_query(question.strip())
        if not normalized:
            raise ValueError("question must not be empty")
        if len(normalized) > self.settings.max_question_chars:
            raise ValueError(f"question exceeds {self.settings.max_question_chars} characters")
        return normalized

    def prepare_question(self, question: str) -> str:
        """Public normalization entry point used by the agent team."""

        return self._prepare_question(question)

    def retrieve(
        self,
        question: str,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        as_of: date | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve context for a single subtask without generating an answer."""

        prepared = self._prepare_question(question)
        resolved_top_k = top_k or self.settings.top_k
        if not 1 <= resolved_top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        return self.retriever.retrieve(
            prepared,
            top_k=resolved_top_k,
            document_ids=document_ids,
            as_of=as_of or date.today(),
        )

    def answer(
        self,
        question: str,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        as_of: date | None = None,
    ) -> RagResult:
        started = time.perf_counter()
        resolved_as_of = as_of or date.today()
        prepared = self._prepare_question(question)
        resolved_top_k = top_k or self.settings.top_k
        if not 1 <= resolved_top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")

        if looks_like_prompt_injection(prepared):
            return self._blocked_result(prepared, resolved_as_of, started)

        retrieved = self.retriever.retrieve(
            prepared,
            top_k=resolved_top_k,
            document_ids=document_ids,
            as_of=resolved_as_of,
        )
        return self.answer_from_retrieved(
            prepared,
            retrieved,
            as_of=resolved_as_of,
            started=started,
        )

    def answer_from_retrieved(
        self,
        question: str,
        retrieved: list[RetrievedChunk],
        *,
        as_of: date | None = None,
        started: float | None = None,
    ) -> RagResult:
        """Generate and validate an answer from an existing retrieval bundle.

        The agent team uses this method after its Retriever Agent has collected
        evidence, avoiding a second retrieval pass.
        """

        started = started or time.perf_counter()
        prepared = self._prepare_question(question)
        resolved_as_of = as_of or date.today()
        top_score = retrieved[0].score if retrieved else 0.0
        top_lexical_score = retrieved[0].lexical_score if retrieved else 0.0
        query_coverage = self.query_coverage(prepared, retrieved)
        base_metrics: dict[str, Any] = {
            "retrieval_top_score": top_score,
            "retrieval_top_lexical_score": top_lexical_score,
            "query_coverage": round(query_coverage, 4),
            "retrieved_count": len(retrieved),
            "citation_count": 0,
            "context_chars": 0,
            "sanitized_chunks": 0,
            "security_event": None,
            "refusal_reason": None,
            "as_of": resolved_as_of.isoformat(),
        }

        low_semantic_score = top_score < self.settings.min_retrieval_score
        low_query_coverage = query_coverage < self.settings.min_query_coverage
        if not retrieved or low_semantic_score or low_query_coverage:
            if not retrieved:
                refusal_reason = "no_retrieval"
            elif low_query_coverage:
                refusal_reason = "low_query_coverage"
            else:
                refusal_reason = "low_retrieval_score"
            return self._refusal_result(
                NO_CONTEXT_ANSWER,
                retrieved,
                resolved_as_of,
                started,
                {**base_metrics, "refusal_reason": refusal_reason},
                security_blocked=False,
            )

        items, sanitized_chunks = build_context_items(
            retrieved,
            max_chars=self.settings.max_context_chars,
        )
        base_metrics["sanitized_chunks"] = sanitized_chunks
        base_metrics["context_chars"] = sum(len(item.text) for item in items)

        if not items:
            return self._refusal_result(
                UNSAFE_CONTEXT_ANSWER,
                retrieved,
                resolved_as_of,
                started,
                {**base_metrics, "security_event": "unsafe_retrieved_context"},
                security_blocked=True,
            )

        generation: Generation = self.generator.generate(prepared, items)
        (
            text,
            used_indices,
            invalid_count,
            raw_marker_count,
            raw_text,
        ) = self._validate_generation(generation, len(items))
        item_by_index = {item.index: item for item in items}
        citations = [
            Citation(
                index=index,
                chunk_id=item_by_index[index].chunk.chunk_id,
                document_id=item_by_index[index].chunk.document_id,
                title=item_by_index[index].chunk.title,
                source=item_by_index[index].chunk.source,
                section=item_by_index[index].chunk.section,
                policy_key=item_by_index[index].chunk.policy_key,
                version=item_by_index[index].chunk.version,
                effective_from=item_by_index[index].chunk.effective_from,
                effective_until=item_by_index[index].chunk.effective_until,
                snippet=item_by_index[index].text[:280],
                score=item_by_index[index].chunk.score,
                quote=generation.quotes.get(index),
                quote_verified=bool(
                    generation.quotes.get(index)
                    and generation.quotes[index] in item_by_index[index].text
                ),
            )
            for index in used_indices
            if index in item_by_index
        ]

        invalid_rate = invalid_count / raw_marker_count if raw_marker_count else 0.0
        metrics = {
            **base_metrics,
            "citation_count": len(citations),
            "cited_sources": sorted({citation.source for citation in citations}),
            "raw_citation_count": raw_marker_count,
            "invalid_citation_count": invalid_count,
            "invalid_citation_rate": round(invalid_rate, 4),
            "fallback_used": generation.fallback_used,
        }
        return RagResult(
            answer=text,
            refused=generation.refused,
            security_blocked=False,
            as_of=resolved_as_of,
            citations=citations,
            retrieved=retrieved,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version=PROMPT_VERSION,
            mode=self.generator.mode,
            metrics=metrics,
            raw_answer=raw_text,
            invalid_citation_count=invalid_count,
            invalid_citation_rate=invalid_rate,
            fallback_used=generation.fallback_used,
        )

    def _blocked_result(self, question: str, as_of: date, started: float) -> RagResult:
        return RagResult(
            answer=INJECTION_ANSWER,
            refused=True,
            security_blocked=True,
            as_of=as_of,
            citations=[],
            retrieved=[],
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version=PROMPT_VERSION,
            mode=self.generator.mode,
            metrics={
                "retrieval_top_score": 0.0,
                "retrieval_top_lexical_score": 0.0,
                "query_coverage": 0.0,
                "retrieved_count": 0,
                "citation_count": 0,
                "context_chars": 0,
                "sanitized_chunks": 0,
                "security_event": "direct_prompt_injection",
                "refusal_reason": "prompt_injection",
                "as_of": as_of.isoformat(),
                "normalized_question": question,
            },
        )

    def _refusal_result(
        self,
        answer: str,
        retrieved: list[RetrievedChunk],
        as_of: date,
        started: float,
        metrics: dict[str, Any],
        *,
        security_blocked: bool,
    ) -> RagResult:
        return RagResult(
            answer=answer,
            refused=True,
            security_blocked=security_blocked,
            as_of=as_of,
            citations=[],
            retrieved=retrieved,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version=PROMPT_VERSION,
            mode=self.generator.mode,
            metrics=metrics,
        )

    @staticmethod
    def query_coverage(question: str, retrieved: list[RetrievedChunk]) -> float:
        """Fraction of query terms that appear anywhere in retrieved context."""

        query_tokens = set(lexical_tokens(question))
        if not query_tokens:
            return 1.0
        context_tokens: set[str] = set()
        for chunk in retrieved:
            context_tokens.update(lexical_tokens(chunk.content))
        return len(query_tokens.intersection(context_tokens)) / len(query_tokens)

    @staticmethod
    def _validate_generation(
        generation: Generation,
        context_count: int,
    ) -> tuple[str, list[int], int, int, str]:
        """Remove impossible citation numbers, preserving raw audit counters."""

        raw_text = generation.text.strip()
        text = raw_text or NO_CONTEXT_ANSWER

        raw_markers = [int(match) for match in _CITATION_RE.findall(text)]
        invalid = [marker for marker in raw_markers if marker < 1 or marker > context_count]
        for marker in set(invalid):
            text = text.replace(f"[{marker}]", "")

        used = {index for index in generation.used_indices if 1 <= index <= context_count}
        used.update(
            marker for marker in raw_markers if 1 <= marker <= context_count
        )
        return text.strip(), sorted(used), len(invalid), len(raw_markers), raw_text
