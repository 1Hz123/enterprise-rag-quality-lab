"""Auditor agent: validate grounding, citations, dates and safety."""

from __future__ import annotations

import re
import time
from datetime import date

from app.agents.types import AgentTrace, AuditReport, Plan, SubAnswer
from app.rag.intent import answer_covers_focus
from app.rag.pipeline import Citation, RagResult

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


def _covers(citation: Citation, as_of: date) -> bool:
    try:
        if citation.effective_from and date.fromisoformat(citation.effective_from) > as_of:
            return False
        if citation.effective_until and date.fromisoformat(citation.effective_until) < as_of:
            return False
    except ValueError:
        return False
    return True


class AuditorAgent:
    name = "auditor"
    responsibility = "检查引用完整性、制度时效、跨任务覆盖与安全边界，决定是否沉淀经验。"

    def audit(
        self,
        plan: Plan,
        result: RagResult,
        sub_answers: list[SubAnswer],
        *,
        cache_hit: bool = False,
    ) -> tuple[AuditReport, AgentTrace]:
        started = time.perf_counter()
        findings: list[str] = []
        checks: dict[str, bool] = {}

        checks["non_empty_answer"] = bool(result.answer.strip())
        if not checks["non_empty_answer"]:
            findings.append("answer is empty")

        if result.refused:
            checks["safe_refusal"] = not result.citations
            checks["citations_present"] = True
            checks["markers_valid"] = True
            checks["quotes_supported"] = True
            checks["no_invalid_citations"] = result.invalid_citation_count == 0
            checks["answer_relevant"] = True
            checks["temporal_valid"] = True
            checks["multi_task_coverage"] = True
            checks["security_clear"] = True
            if result.citations:
                findings.append("refusal unexpectedly contains citations")
            quality = 0.5
        else:
            checks["safe_refusal"] = True
            checks["citations_present"] = bool(result.citations)
            markers = {int(match) for match in _CITATION_RE.findall(result.answer)}
            citation_indices = {citation.index for citation in result.citations}
            checks["markers_valid"] = bool(markers) and markers.issubset(citation_indices)
            checks["quotes_supported"] = all(
                not citation.quote or citation.quote_verified
                for citation in result.citations
            )
            checks["no_invalid_citations"] = result.invalid_citation_count == 0
            question = plan.subtasks[0].question if plan.subtasks else ""
            citation_context = "\n".join(
                f"{citation.title}\n{citation.section}\n{citation.snippet}"
                for citation in result.citations
            )
            checks["answer_relevant"] = answer_covers_focus(
                question,
                result.answer,
                context=citation_context,
            )
            checks["temporal_valid"] = self._temporal_valid(plan, result, sub_answers)
            checks["multi_task_coverage"] = self._multi_task_coverage(plan, sub_answers)
            checks["security_clear"] = not result.security_blocked

            if not checks["citations_present"]:
                findings.append("answer has no citations")
            if not checks["markers_valid"]:
                findings.append("citation markers do not match citation list")
            if not checks["quotes_supported"]:
                findings.append("citation quote is not present in its chunk")
            if not checks["no_invalid_citations"]:
                findings.append("generation emitted invalid citation markers")
            if not checks["answer_relevant"]:
                findings.append("answer does not cover the question focus")
            if not checks["temporal_valid"]:
                findings.append("citation is outside its effective window")
            if not checks["multi_task_coverage"]:
                findings.append("not every planned subtask contributed evidence")
            if not checks["security_clear"]:
                findings.append("security block present on a non-refusal answer")

            quality = 0.50
            quality += 0.20 if checks["citations_present"] else 0
            quality += 0.15 if checks["markers_valid"] else 0
            quality += 0.10 if checks["temporal_valid"] else 0
            quality += 0.05 if checks["multi_task_coverage"] else 0
            if not checks["answer_relevant"]:
                quality = min(quality, 0.5)
            quality = round(min(1.0, quality), 4)

        passed = all(checks.values())
        report = AuditReport(
            passed=passed,
            quality_score=quality,
            checks=checks,
            findings=findings,
        )
        trace = AgentTrace(
            agent=self.name,
            action="audit",
            status="passed" if passed else "needs_attention",
            detail=(
                f"引用 {len(result.citations)} 个，质量分 {quality:.2f}"
                if not result.refused
                else "安全/无资料拒答，未沉淀经验"
            ),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            metadata={
                "checks": checks,
                "findings": findings,
                "quality_score": quality,
                "cache_hit": cache_hit,
            },
        )
        return report, trace

    @staticmethod
    def _temporal_valid(plan: Plan, result: RagResult, sub_answers: list[SubAnswer]) -> bool:
        if result.citations and len(sub_answers) <= 1:
            return all(_covers(citation, result.as_of) for citation in result.citations)
        for sub_answer in sub_answers:
            for citation in sub_answer.result.citations:
                if not _covers(citation, sub_answer.subtask.as_of):
                    return False
        return True

    @staticmethod
    def _multi_task_coverage(plan: Plan, sub_answers: list[SubAnswer]) -> bool:
        if len(plan.subtasks) <= 1:
            return True
        if len(sub_answers) != len(plan.subtasks):
            return False
        return all(
            not sub_answer.result.refused and bool(sub_answer.result.citations)
            for sub_answer in sub_answers
        )
