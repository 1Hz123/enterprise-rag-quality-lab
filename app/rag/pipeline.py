"""End-to-end RAG pipeline with guardrails and citation validation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.config import Settings
from app.core.cache import CacheManager, CacheRequest
from app.knowledge.embeddings import lexical_tokens
from app.knowledge.retriever import RetrievedChunk, Retriever
from app.knowledge.splitter import estimate_tokens
from app.rag.generator import Generation, Generator
from app.rag.intent import (
    CLARIFICATION_ANSWER,
    focus_coverage,
    is_ambiguous_leave_question,
)
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
        *,
        cache: CacheManager | None = None,
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.settings = settings
        self.cache = cache

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
        scope = self.cache.request_scope() if self.cache else nullcontext()
        with scope:
            result = self._answer_impl(
                question,
                top_k=top_k,
                document_ids=document_ids,
                as_of=as_of,
            )
            return self._attach_cache_metrics(result)

    def _answer_impl(
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
        if is_ambiguous_leave_question(prepared):
            return self._clarification_result(prepared, resolved_as_of, started)

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

        scope = self.cache.request_scope() if self.cache else nullcontext()
        with scope:
            result = self._answer_from_retrieved_impl(
                question,
                retrieved,
                as_of=as_of,
                started=started,
            )
            return self._attach_cache_metrics(result)

    def _answer_from_retrieved_impl(
        self,
        question: str,
        retrieved: list[RetrievedChunk],
        *,
        as_of: date | None = None,
        started: float | None = None,
    ) -> RagResult:
        started = started or time.perf_counter()
        prepared = self._prepare_question(question)
        if is_ambiguous_leave_question(prepared):
            return self._clarification_result(prepared, as_of or date.today(), started)
        resolved_as_of = as_of or date.today()
        top_score = retrieved[0].score if retrieved else 0.0
        top_lexical_score = retrieved[0].lexical_score if retrieved else 0.0
        query_coverage = self.query_coverage(prepared, retrieved)
        focus, focus_coverage_value = focus_coverage(
            prepared,
            (chunk.content for chunk in retrieved),
        )
        base_metrics: dict[str, Any] = {
            "retrieval_top_score": top_score,
            "retrieval_top_lexical_score": top_lexical_score,
            "query_coverage": round(query_coverage, 4),
            "focus_tokens": focus,
            "focus_coverage": round(focus_coverage_value, 4),
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
        no_focus_hit = bool(focus) and focus_coverage_value <= 0.0
        if not retrieved or low_semantic_score or low_query_coverage or no_focus_hit:
            if not retrieved:
                refusal_reason = "no_retrieval"
            elif low_query_coverage:
                refusal_reason = "low_query_coverage"
            elif no_focus_hit:
                refusal_reason = "low_focus_coverage"
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

        if self.cache is not None and self.cache.enabled:
            cache_key = self._answer_cache_key(prepared, items, resolved_as_of)
            saved_input_tokens = sum(estimate_tokens(item.text) for item in items)

            def load() -> bytes:
                generated = self._generate_from_items(
                    prepared,
                    items,
                    retrieved,
                    resolved_as_of,
                    started,
                    base_metrics,
                )
                return json.dumps(
                    self._serialize_answer(generated),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")

            values = self.cache.get_many_or_load(
                "answer",
                [
                    CacheRequest(
                        key=cache_key,
                        loader=load,
                        saved_input_tokens=saved_input_tokens,
                        saved_model_calls=1,
                    )
                ],
                ttl=self.cache.ttl("answer"),
            )
            read = values.get(cache_key)
            if read is not None and read.value is not None:
                try:
                    cached = self._deserialize_answer(
                        read.value,
                        as_of=resolved_as_of,
                        started=started,
                    )
                    if read.hit:
                        self.cache.record_saved(
                            "answer",
                            saved_output_tokens=estimate_tokens(cached.answer),
                        )
                    return cached
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    self.cache.delete("answer", cache_key)

        return self._generate_from_items(
            prepared,
            items,
            retrieved,
            resolved_as_of,
            started,
            base_metrics,
        )

    def _generate_from_items(
        self,
        question: str,
        items: list[Any],
        retrieved: list[RetrievedChunk],
        as_of: date,
        started: float,
        base_metrics: dict[str, Any],
    ) -> RagResult:
        generation: Generation = self.generator.generate(question, items)
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
            as_of=as_of,
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

    def _answer_cache_key(
        self,
        question: str,
        items: list[Any],
        as_of: date,
    ) -> str:
        assert self.cache is not None
        context = [
            (
                item.index,
                item.chunk.chunk_id,
                item.chunk.score,
                hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            )
            for item in items
        ]
        index_version = getattr(self.retriever, "index_version", lambda: None)()
        return self.cache.key(
            "answer",
            {
                "answer_schema": 2,
                "question": question,
                "context": context,
                "index_version": index_version,
                "prompt_version": PROMPT_VERSION,
                "generator": self._generator_signature(),
                "as_of": as_of.isoformat(),
            },
        )

    def _generator_signature(self) -> str:
        return str(getattr(self.generator, "cache_signature", self.generator.mode))

    @staticmethod
    def _serialize_answer(result: RagResult) -> dict[str, Any]:
        metrics = {
            key: value
            for key, value in result.metrics.items()
            if key not in {"cache", "cache_hit", "cache_layers"}
        }
        return {
            "schema_version": 2,
            "answer": result.answer,
            "raw_answer": result.raw_answer,
            "refused": result.refused,
            "security_blocked": result.security_blocked,
            "citations": [asdict(citation) for citation in result.citations],
            "retrieved": [chunk.to_dict() for chunk in result.retrieved],
            "prompt_version": result.prompt_version,
            "mode": result.mode,
            "metrics": metrics,
            "invalid_citation_count": result.invalid_citation_count,
            "invalid_citation_rate": result.invalid_citation_rate,
            "fallback_used": result.fallback_used,
        }

    @staticmethod
    def _deserialize_answer(
        payload: bytes,
        *,
        as_of: date,
        started: float,
    ) -> RagResult:
        data = json.loads(payload.decode("utf-8"))
        if data.get("schema_version") != 2:
            raise ValueError("unsupported answer cache schema")
        citations = data.get("citations")
        retrieved = data.get("retrieved")
        metrics = data.get("metrics")
        if not isinstance(citations, list) or not isinstance(retrieved, list):
            raise ValueError("answer cache payload is incomplete")
        if not isinstance(metrics, dict):
            raise ValueError("answer cache metrics must be an object")
        return RagResult(
            answer=str(data["answer"]),
            refused=bool(data["refused"]),
            security_blocked=bool(data["security_blocked"]),
            as_of=as_of,
            citations=[Citation(**citation) for citation in citations],
            retrieved=[RetrievedChunk(**chunk) for chunk in retrieved],
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version=str(data["prompt_version"]),
            mode=str(data["mode"]),
            metrics=metrics,
            raw_answer=str(data.get("raw_answer", "")),
            invalid_citation_count=int(data.get("invalid_citation_count", 0)),
            invalid_citation_rate=float(data.get("invalid_citation_rate", 0.0)),
            fallback_used=bool(data.get("fallback_used", False)),
        )

    def _attach_cache_metrics(self, result: RagResult) -> RagResult:
        if self.cache is None:
            return result
        metrics = self.cache.request_metrics()
        result.metrics["cache"] = metrics
        result.metrics["cache_hit"] = metrics["hit"]
        result.metrics["cache_layers"] = [
            layer
            for layer, layer_metrics in metrics["layers"].items()
            if layer_metrics["hits"] > 0
        ]
        return result

    def clarification_result(
        self,
        question: str,
        *,
        as_of: date | None = None,
        started: float | None = None,
    ) -> RagResult:
        """Public clarification result for ambiguous leave questions."""

        return self._clarification_result(
            question,
            as_of or date.today(),
            started or time.perf_counter(),
        )

    def _clarification_result(self, question: str, as_of: date, started: float) -> RagResult:
        return RagResult(
            answer=CLARIFICATION_ANSWER,
            refused=True,
            security_blocked=False,
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
                "focus_tokens": [],
                "focus_coverage": 0.0,
                "retrieved_count": 0,
                "citation_count": 0,
                "context_chars": 0,
                "sanitized_chunks": 0,
                "security_event": None,
                "refusal_reason": "ambiguous_leave_question",
                "needs_clarification": True,
                "as_of": as_of.isoformat(),
                "normalized_question": question,
            },
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
