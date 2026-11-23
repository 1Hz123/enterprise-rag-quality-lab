"""Reasoner agent: turn evidence bundles into a grounded answer."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import date

from app.agents.types import AgentTrace, Plan, RetrievalBundle, SubAnswer
from app.knowledge.retriever import RetrievedChunk
from app.rag.pipeline import Citation, RAGPipeline, RagResult

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_FACT_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>个工作日|天|个月|小时|分钟|元|%)"
)


def _facts(text: str) -> list[str]:
    cleaned = _CITATION_RE.sub("", text)
    return list(dict.fromkeys(match.group(0) for match in _FACT_RE.finditer(cleaned)))


def _renumber_text(text: str, mapping: dict[int, int]) -> str:
    def replace_marker(match: re.Match[str]) -> str:
        old_index = int(match.group(1))
        new_index = mapping.get(old_index)
        return f"[{new_index}]" if new_index else ""

    return _CITATION_RE.sub(replace_marker, text)


class ReasonerAgent:
    name = "reasoner"
    responsibility = "基于检索证据生成单跳答案，并完成多时点对比与引用合并。"

    def __init__(self, pipeline: RAGPipeline) -> None:
        self.pipeline = pipeline

    def answer(
        self,
        plan: Plan,
        bundles: list[RetrievalBundle],
        *,
        started: float | None = None,
        final_as_of: date | None = None,
    ) -> tuple[RagResult, list[SubAnswer], AgentTrace]:
        started = started or time.perf_counter()
        sub_answers: list[SubAnswer] = []
        for bundle in bundles:
            subtask_started = time.perf_counter()
            result = self.pipeline.answer_from_retrieved(
                bundle.subtask.question,
                bundle.chunks,
                as_of=bundle.subtask.as_of,
                started=subtask_started,
            )
            sub_answers.append(SubAnswer(subtask=bundle.subtask, result=result, retrieval=bundle))

        if plan.strategy == "single_question" or len(sub_answers) == 1:
            result = sub_answers[0].result
            result.mode = "organization"
            result.metrics = {
                **result.metrics,
                "plan_strategy": plan.strategy,
                "subtask_count": 1,
            }
            trace = AgentTrace(
                agent=self.name,
                action="synthesize",
                status="refused" if result.refused else "completed",
                detail="单跳问题直接生成并验证引用",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                metadata={"subtask_count": 1},
            )
            return result, sub_answers, trace

        if any(sub_answer.result.refused for sub_answer in sub_answers):
            result = RagResult(
                answer="部分时点的制度资料不足，无法完成完整对比。",
                refused=True,
                security_blocked=False,
                as_of=final_as_of or plan.as_of,
                citations=[],
                retrieved=[chunk for bundle in bundles for chunk in bundle.chunks],
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                prompt_version=sub_answers[0].result.prompt_version,
                mode="organization",
                metrics={
                    "plan_strategy": plan.strategy,
                    "subtask_count": len(sub_answers),
                    "sub_answers": [answer.to_dict() for answer in sub_answers],
                    "refusal_reason": "partial_temporal_evidence",
                },
            )
            trace = AgentTrace(
                agent=self.name,
                action="synthesize",
                status="refused",
                detail="至少一个时点缺少可依据资料，拒绝拼接不完整对比",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                metadata={"subtask_count": len(sub_answers)},
            )
            return result, sub_answers, trace

        global_citations: list[Citation] = []
        citation_by_chunk: dict[str, Citation] = {}
        sections: list[str] = []
        for sub_answer in sub_answers:
            # First establish the global citation index for this subtask.
            for citation in sub_answer.result.citations:
                if citation.chunk_id not in citation_by_chunk:
                    new_citation = replace(citation, index=len(global_citations) + 1)
                    global_citations.append(new_citation)
                    citation_by_chunk[citation.chunk_id] = new_citation
            mapping = {
                citation.index: citation_by_chunk[citation.chunk_id].index
                for citation in sub_answer.result.citations
            }
            renumbered = _renumber_text(sub_answer.result.answer, mapping)
            sections.append(
                f"- {sub_answer.subtask.label}（as_of {sub_answer.subtask.as_of.isoformat()}）："
                f"{renumbered.strip()}"
            )

        comparison, comparison_data = self._compare(sub_answers)
        answer = (
            "按制度生效日期对比：\n"
            + "\n".join(sections)
            + f"\n对比结论：{comparison}"
        )
        retrieved: list[RetrievedChunk] = []
        seen_chunks: set[str] = set()
        for sub_answer in sub_answers:
            for chunk in sub_answer.result.retrieved:
                if chunk.chunk_id not in seen_chunks:
                    retrieved.append(chunk)
                    seen_chunks.add(chunk.chunk_id)

        result = RagResult(
            answer=answer,
            refused=False,
            security_blocked=any(answer.result.security_blocked for answer in sub_answers),
            as_of=final_as_of or plan.as_of,
            citations=global_citations,
            retrieved=retrieved,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_version=sub_answers[0].result.prompt_version,
            mode="organization",
            metrics={
                "plan_strategy": plan.strategy,
                "subtask_count": len(sub_answers),
                "sub_answers": [answer.to_dict() for answer in sub_answers],
                "citation_count": len(global_citations),
                "cited_sources": sorted({citation.source for citation in global_citations}),
                "retrieval_top_score": max(
                    (answer.result.metrics.get("retrieval_top_score", 0) for answer in sub_answers),
                    default=0.0,
                ),
                "query_coverage": min(
                    (answer.retrieval.coverage for answer in sub_answers),
                    default=0.0,
                ),
                "as_of": (final_as_of or plan.as_of).isoformat(),
                "comparison": comparison_data,
                "invalid_citation_count": sum(
                    answer.result.invalid_citation_count for answer in sub_answers
                ),
                "invalid_citation_rate": max(
                    (answer.result.invalid_citation_rate for answer in sub_answers),
                    default=0.0,
                ),
                "fallback_used": any(
                    answer.result.fallback_used for answer in sub_answers
                ),
                "cache_hit": any(
                    bool(answer.result.metrics.get("cache_hit")) for answer in sub_answers
                ),
            },
            invalid_citation_count=sum(
                answer.result.invalid_citation_count for answer in sub_answers
            ),
            invalid_citation_rate=max(
                (answer.result.invalid_citation_rate for answer in sub_answers),
                default=0.0,
            ),
            fallback_used=any(answer.result.fallback_used for answer in sub_answers),
            raw_answer=answer,
        )
        trace = AgentTrace(
            agent=self.name,
            action="synthesize",
            status="completed",
            detail=f"合并 {len(sub_answers)} 个时点答案，生成 {len(global_citations)} 个全局引用",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            metadata={"comparison": comparison, "subtask_count": len(sub_answers)},
        )
        return result, sub_answers, trace

    @staticmethod
    def _compare(sub_answers: list[SubAnswer]) -> tuple[str, dict[str, object]]:
        """Compare numeric facts unit-by-unit and keep a structured audit record."""

        facts_by_label = [
            (answer.subtask.label, _facts(answer.result.answer)) for answer in sub_answers
        ]
        parsed: dict[str, dict[str, str]] = {}
        for label, facts in facts_by_label:
            by_unit: dict[str, str] = {}
            for fact in facts:
                match = _FACT_RE.fullmatch(fact)
                if match:
                    by_unit[match.group("unit")] = match.group("value")
            parsed[label] = by_unit

        units = sorted({unit for values in parsed.values() for unit in values})
        changed: list[dict[str, str]] = []
        unchanged: list[dict[str, str]] = []
        for unit in units:
            values = {
                label: values[unit]
                for label, values in parsed.items()
                if unit in values
            }
            if len(set(values.values())) > 1:
                changed.append({"unit": unit, **values})
            elif values:
                unchanged.append({"unit": unit, **values})

        texts = [answer.result.answer.strip() for answer in sub_answers]
        data: dict[str, object] = {
            "labels": [label for label, _ in facts_by_label],
            "facts": {label: facts for label, facts in facts_by_label},
            "changed": changed,
            "unchanged": unchanged,
            "text_changed": len(set(texts)) > 1,
        }
        if changed:
            groups: list[str] = []
            for row in changed:
                unit = str(row["unit"])
                labels = [
                    f"{label} {value}{unit}"
                    for label, value in row.items()
                    if label != "unit"
                ]
                groups.append("、".join(labels))
            text = "关键数值变化：" + "；".join(groups)
            if unchanged:
                text += "。未变：" + "、".join(str(row["unit"]) for row in unchanged)
            return text, data
        if unchanged:
            groups = []
            for row in unchanged:
                unit = str(row["unit"])
                values = [value for key, value in row.items() if key != "unit"]
                groups.append(f"{unit} " + "/".join(values))
            return "关键数值未变：" + "、".join(groups), data
        if len(set(texts)) > 1:
            return "两个以上时点的制度表述存在差异，具体以各版本引用为准。", data
        return "各时点的规定未发现差异。", data
