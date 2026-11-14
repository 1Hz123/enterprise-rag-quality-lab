"""Retriever agent: execute each planned task against the hybrid retriever."""

from __future__ import annotations

import time

from app.agents.types import AgentTrace, Plan, RetrievalBundle
from app.rag.pipeline import RAGPipeline


class RetrieverAgent:
    name = "retriever"
    responsibility = "按子任务执行向量 + BM25 混合检索，并应用生效日期过滤。"

    def __init__(self, pipeline: RAGPipeline) -> None:
        self.pipeline = pipeline

    def retrieve(
        self,
        plan: Plan,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
    ) -> tuple[list[RetrievalBundle], AgentTrace]:
        started = time.perf_counter()
        bundles: list[RetrievalBundle] = []
        for subtask in plan.subtasks:
            subtask_started = time.perf_counter()
            chunks = self.pipeline.retrieve(
                subtask.question,
                top_k=top_k,
                document_ids=document_ids,
                as_of=subtask.as_of,
            )
            normalized_question = self.pipeline.prepare_question(subtask.question)
            coverage = self.pipeline.query_coverage(normalized_question, chunks)
            bundles.append(
                RetrievalBundle(
                    subtask=subtask,
                    chunks=chunks,
                    top_score=chunks[0].score if chunks else 0.0,
                    coverage=coverage,
                    latency_ms=round((time.perf_counter() - subtask_started) * 1000, 2),
                )
            )

        total_chunks = sum(len(bundle.chunks) for bundle in bundles)
        trace = AgentTrace(
            agent=self.name,
            action="retrieve",
            status="completed" if total_chunks else "empty",
            detail=f"完成 {len(bundles)} 个子任务的混合检索，共 {total_chunks} 个候选块",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            metadata={"bundles": [bundle.to_dict() for bundle in bundles]},
        )
        return bundles, trace
