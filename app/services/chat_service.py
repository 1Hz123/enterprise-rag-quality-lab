"""Chat orchestration and run persistence."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any

from app.agents.orchestrator import IntelligentOrganization
from app.db import Database, utc_now
from app.schemas import (
    AgentTraceOut,
    ChatRequest,
    ChatResponse,
    CitationOut,
    RetrievedChunkOut,
    RunDetail,
    RunList,
    RunSummary,
)


class RunNotFoundError(LookupError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run not found: {run_id}")
        self.run_id = run_id


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class ChatService:
    def __init__(self, db: Database, organization: IntelligentOrganization) -> None:
        self.db = db
        self.organization = organization

    def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            result = self.organization.answer(
                request.question,
                top_k=request.top_k,
                document_ids=request.document_ids,
                as_of=request.as_of,
            )
        except Exception as exc:
            self._record_failure(request.question, exc)
            raise

        run_id = f"run_{uuid.uuid4().hex}"
        created_at = utc_now()
        retrieval = [chunk.to_dict() for chunk in result.retrieved]
        citations = [asdict(citation) for citation in result.citations]
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs
                    (id, question, answer, refused, security_blocked, status, mode,
                     retrieval_json, citations_json, metrics_json, prompt_version,
                     latency_ms, error, created_at)
                VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    run_id,
                    request.question,
                    result.answer,
                    int(result.refused),
                    int(result.security_blocked),
                    result.mode,
                    json.dumps(retrieval, ensure_ascii=False),
                    json.dumps(citations, ensure_ascii=False),
                    json.dumps(result.metrics, ensure_ascii=False),
                    result.prompt_version,
                    result.latency_ms,
                    created_at,
                ),
            )

        return ChatResponse(
            run_id=run_id,
            question=request.question,
            answer=result.answer,
            refused=result.refused,
            security_blocked=result.security_blocked,
            as_of=result.as_of,
            citations=[CitationOut(**citation) for citation in citations],
            retrieved_chunks=[RetrievedChunkOut(**chunk) for chunk in retrieval],
            latency_ms=result.latency_ms,
            prompt_version=result.prompt_version,
            mode=result.mode,
            created_at=created_at,
            plan_strategy=result.plan_strategy,
            quality_score=result.quality_score,
            experience_reused=result.experience_reused,
            experience_ids=result.experience_ids,
            agents=[
                AgentTraceOut(
                    agent=trace.agent,
                    action=trace.action,
                    status=trace.status,
                    detail=trace.detail,
                    latency_ms=trace.latency_ms,
                    metadata=trace.metadata,
                )
                for trace in result.agents
            ],
        )

    def _record_failure(self, question: str, exc: Exception) -> None:
        run_id = f"run_{uuid.uuid4().hex}"
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs
                    (id, question, answer, refused, security_blocked, status, mode,
                     retrieval_json, citations_json, metrics_json, prompt_version,
                     latency_ms, error, created_at)
                VALUES (?, ?, '', 0, 0, 'failed', ?, '[]', '[]', '{}', 'unknown', 0, ?, ?)
                """,
                (
                    run_id,
                    question,
                    "organization",
                    f"{type(exc).__name__}: {exc}"[:1000],
                    utc_now(),
                ),
            )

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> RunList:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        rows = self.db.fetch_all(
            "SELECT * FROM runs ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        total_row = self.db.fetch_one("SELECT COUNT(*) AS n FROM runs")
        items = []
        for row in rows:
            citations = _load_json(row["citations_json"], [])
            items.append(
                RunSummary(
                    run_id=row["id"],
                    question=row["question"],
                    answer_preview=row["answer"][:180],
                    refused=bool(row["refused"]),
                    security_blocked=bool(row["security_blocked"]),
                    status=row["status"],
                    mode=row["mode"],
                    citation_count=len(citations) if isinstance(citations, list) else 0,
                    latency_ms=float(row["latency_ms"]),
                    created_at=row["created_at"],
                )
            )
        return RunList(
            items=items,
            total=int(total_row["n"]) if total_row else 0,
            limit=limit,
            offset=offset,
        )

    def get_run(self, run_id: str) -> RunDetail:
        row = self.db.fetch_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not row:
            raise RunNotFoundError(run_id)
        citations = _load_json(row["citations_json"], [])
        return RunDetail(
            run_id=row["id"],
            question=row["question"],
            answer_preview=row["answer"][:180],
            answer=row["answer"],
            refused=bool(row["refused"]),
            security_blocked=bool(row["security_blocked"]),
            status=row["status"],
            mode=row["mode"],
            citation_count=len(citations) if isinstance(citations, list) else 0,
            latency_ms=float(row["latency_ms"]),
            created_at=row["created_at"],
            retrieval=_load_json(row["retrieval_json"], []),
            citations=citations,
            metrics=_load_json(row["metrics_json"], {}),
            prompt_version=row["prompt_version"],
            error=row["error"],
        )
