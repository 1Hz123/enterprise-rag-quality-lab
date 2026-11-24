"""Intelligent organization runtime: coordinate, retrieve, reason, audit, learn."""

from __future__ import annotations

import contextlib
import time
from datetime import date
from typing import Any

from app.agents.auditor import AuditorAgent
from app.agents.coordinator import CoordinatorAgent
from app.agents.experience import ExperienceService
from app.agents.reasoner import ReasonerAgent
from app.agents.retriever_agent import RetrieverAgent
from app.agents.types import (
    AgentTrace,
    ExperienceRecord,
    OrganizationResult,
    Plan,
    Subtask,
)
from app.config import Settings
from app.rag.intent import is_ambiguous_leave_question
from app.rag.pipeline import RAGPipeline, RagResult
from app.rag.prompt import INJECTION_ANSWER, PROMPT_VERSION, looks_like_prompt_injection
from app.rag.query import normalize_query


class IntelligentOrganization:
    """A small task-based organization around the existing RAG tool layer.

    Roles are intentionally explicit:
    - coordinator: task decomposition and dispatch
    - retriever: evidence collection
    - reasoner: synthesis and cross-version comparison
    - auditor: grounding/date/safety validation
    - experience library: organizational memory and guarded reuse
    """

    def __init__(
        self,
        pipeline: RAGPipeline,
        experience: ExperienceService,
        settings: Settings,
    ) -> None:
        self.pipeline = pipeline
        self.settings = settings
        self.experience = experience
        self.coordinator = CoordinatorAgent()
        self.retriever = RetrieverAgent(pipeline)
        self.reasoner = ReasonerAgent(pipeline)
        self.auditor = AuditorAgent()

    @property
    def roster(self) -> list[dict[str, str]]:
        return [
            {
                "name": self.coordinator.name,
                "responsibility": self.coordinator.responsibility,
            },
            {
                "name": self.retriever.name,
                "responsibility": self.retriever.responsibility,
            },
            {
                "name": self.reasoner.name,
                "responsibility": self.reasoner.responsibility,
            },
            {
                "name": self.auditor.name,
                "responsibility": self.auditor.responsibility,
            },
            {
                "name": "experience",
                "responsibility": "沉淀高质量问答，验证上下文哈希后复用组织经验。",
            },
        ]

    def answer(
        self,
        question: str,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        as_of: date | None = None,
    ) -> OrganizationResult:
        started = time.perf_counter()
        prepared = self.pipeline.prepare_question(question)
        resolved_as_of = as_of or date.today()

        if looks_like_prompt_injection(prepared):
            return self._blocked_result(prepared, resolved_as_of, started)
        if is_ambiguous_leave_question(prepared):
            return self._clarification_organization_result(
                prepared,
                resolved_as_of,
                started,
            )

        if self.settings.experience_reuse_enabled:
            cached = self._try_experience(prepared, resolved_as_of, started)
            if cached:
                return cached

        plan, coordinator_trace = self.coordinator.plan(prepared, resolved_as_of)
        bundles, retriever_trace = self.retriever.retrieve(
            plan,
            top_k=top_k,
            document_ids=document_ids,
        )
        result, sub_answers, reasoner_trace = self.reasoner.answer(
            plan,
            bundles,
            started=started,
            final_as_of=resolved_as_of,
        )
        audit, auditor_trace = self.auditor.audit(plan, result, sub_answers)

        experiences: list[ExperienceRecord] = []
        experience_trace: AgentTrace | None = None
        if (
            audit.passed
            and not result.refused
            and plan.strategy == "single_question"
            and audit.quality_score >= self.settings.experience_min_quality
        ):
            try:
                record = self.experience.save_experience(
                    prepared,
                    resolved_as_of,
                    result,
                    quality_score=audit.quality_score,
                    signature=self._experience_signature(),
                )
                if record:
                    experiences.append(record)
                    experience_trace = AgentTrace(
                        agent="experience",
                        action="save",
                        status="stored",
                        detail=f"高质量经验已沉淀：{record.id}",
                        latency_ms=0.0,
                        metadata={"experience_id": record.id},
                    )
            except Exception as exc:  # persistence must not break an answer
                experience_trace = AgentTrace(
                    agent="experience",
                    action="save",
                    status="error",
                    detail=f"经验沉淀失败：{type(exc).__name__}",
                    latency_ms=0.0,
                    metadata={"error": str(exc)[:200]},
                )

        agents = [coordinator_trace, retriever_trace, reasoner_trace, auditor_trace]
        if experience_trace:
            agents.append(experience_trace)

        result.mode = "organization"
        result.metrics = {
            **result.metrics,
            "organization": True,
            "plan_strategy": plan.strategy,
            "subtask_count": len(plan.subtasks),
            "agent_count": len(agents),
            "quality_score": audit.quality_score,
            "quality_checks": audit.checks,
            "quality_findings": audit.findings,
            "experience_reused": False,
            "experience_ids": [record.id for record in experiences],
            "agents": [trace.to_dict() for trace in agents],
        }
        return OrganizationResult(
            answer=result.answer,
            refused=result.refused,
            security_blocked=result.security_blocked,
            as_of=resolved_as_of,
            citations=result.citations,
            retrieved=result.retrieved,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version=result.prompt_version,
            mode="organization",
            plan_strategy=plan.strategy,
            quality_score=audit.quality_score,
            experience_reused=False,
            experience_ids=[record.id for record in experiences],
            agents=agents,
            metrics=result.metrics,
        )

    def _experience_signature(self) -> str:
        return (
            f"{PROMPT_VERSION}|{self.pipeline.generator.mode}|"
            f"{self.settings.embedding_provider}:{self.settings.embedding_dimension}|"
            f"top_k:{self.settings.top_k}|chunk:{self.settings.chunk_size}/"
            f"{self.settings.chunk_overlap}"
        )

    def _try_experience(
        self,
        question: str,
        as_of: date,
        started: float,
    ) -> OrganizationResult | None:
        record = self.experience.find_reusable(
            question,
            as_of,
            signature=self._experience_signature(),
        )
        if not record:
            return None
        result = RagResult(
            answer=record.answer,
            refused=False,
            security_blocked=False,
            as_of=as_of,
            citations=record.citations,
            retrieved=record.retrieved,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version="experience",
            mode="organization",
            metrics={
                "retrieval_top_score": 0.0,
                "query_coverage": 0.0,
                "retrieval_top_lexical_score": 0.0,
                "citation_count": len(record.citations),
                "retrieved_count": len(record.retrieved),
                "context_chars": sum(len(chunk.content) for chunk in record.retrieved),
                "sanitized_chunks": 0,
                "security_event": None,
                "refusal_reason": None,
                "as_of": as_of.isoformat(),
                "experience_reused": True,
                "experience_id": record.id,
            },
        )
        plan = Plan(
            strategy="experience_reuse",
            as_of=as_of,
            subtasks=[
                Subtask(
                    id="experience",
                    question=question,
                    as_of=as_of,
                    label="组织经验",
                    reason="命中高质量经验",
                )
            ],
            rationale="相同问题与相同生效日期命中已验证经验，先通过审计再复用。",
        )
        audit, auditor_trace = self.auditor.audit(plan, result, [], cache_hit=True)
        if not audit.passed:
            if not audit.checks.get("answer_relevant", True):
                # A stored experience from an older pipeline may be grounded
                # but unrelated to the question; do not let it poison future
                # requests.
                with contextlib.suppress(Exception):
                    self.experience.delete_experience(record.id)
            return None

        agents = [
            AgentTrace(
                agent=self.coordinator.name,
                action="recall",
                status="completed",
                detail="命中组织经验，跳过重复检索",
                latency_ms=0.0,
                metadata={"strategy": "experience_reuse"},
            ),
            AgentTrace(
                agent="experience",
                action="reuse",
                status="completed",
                detail=f"复用经验 {record.id}（已使用 {record.uses} 次）",
                latency_ms=0.0,
                metadata={"experience_id": record.id, "uses": record.uses},
            ),
            auditor_trace,
        ]
        result.metrics.update(
            {
                "organization": True,
                "plan_strategy": "experience_reuse",
                "subtask_count": 1,
                "agent_count": len(agents),
                "quality_score": audit.quality_score,
                "quality_checks": audit.checks,
                "quality_findings": audit.findings,
                "experience_ids": [record.id],
                "agents": [trace.to_dict() for trace in agents],
            }
        )
        return OrganizationResult(
            answer=record.answer,
            refused=False,
            security_blocked=False,
            as_of=as_of,
            citations=record.citations,
            retrieved=record.retrieved,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version="experience",
            mode="organization",
            plan_strategy="experience_reuse",
            quality_score=audit.quality_score,
            experience_reused=True,
            experience_ids=[record.id],
            agents=agents,
            metrics=result.metrics,
        )

    def _clarification_organization_result(
        self,
        question: str,
        as_of: date,
        started: float,
    ) -> OrganizationResult:
        result = self.pipeline.clarification_result(
            question,
            as_of=as_of,
            started=started,
        )
        trace = AgentTrace(
            agent=self.coordinator.name,
            action="clarify",
            status="clarification",
            detail="宽泛的“假期”问题缺少具体假期类型，先要求澄清",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            metadata={"reason": "ambiguous_leave_question"},
        )
        metrics = {
            **result.metrics,
            "organization": True,
            "plan_strategy": "clarification",
            "subtask_count": 0,
            "agent_count": 1,
            "quality_score": 0.5,
            "quality_checks": {"needs_clarification": True},
            "quality_findings": ["leave type is missing"],
            "experience_reused": False,
            "experience_ids": [],
            "agents": [trace.to_dict()],
        }
        return OrganizationResult(
            answer=result.answer,
            refused=True,
            security_blocked=False,
            as_of=as_of,
            citations=[],
            retrieved=[],
            latency_ms=result.latency_ms,
            prompt_version="organization",
            mode="organization",
            plan_strategy="clarification",
            quality_score=0.5,
            experience_reused=False,
            experience_ids=[],
            agents=[trace],
            metrics=metrics,
        )

    def _blocked_result(self, question: str, as_of: date, started: float) -> OrganizationResult:
        trace = AgentTrace(
            agent=self.coordinator.name,
            action="guard",
            status="blocked",
            detail="直接提示注入在任务分解前被安全策略拦截",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        metrics: dict[str, Any] = {
            "organization": True,
            "plan_strategy": "blocked",
            "subtask_count": 0,
            "agent_count": 1,
            "quality_score": 0.0,
            "quality_checks": {"prompt_injection_blocked": True},
            "quality_findings": [],
            "experience_reused": False,
            "experience_ids": [],
            "security_event": "direct_prompt_injection",
            "as_of": as_of.isoformat(),
            "normalized_question": normalize_query(question),
            "agents": [trace.to_dict()],
        }
        return OrganizationResult(
            answer=INJECTION_ANSWER,
            refused=True,
            security_blocked=True,
            as_of=as_of,
            citations=[],
            retrieved=[],
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version="organization",
            mode="organization",
            plan_strategy="blocked",
            quality_score=0.0,
            experience_reused=False,
            experience_ids=[],
            agents=[trace],
            metrics=metrics,
        )
