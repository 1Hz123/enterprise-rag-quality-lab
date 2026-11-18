"""Coordinator: decompose a question into task-based subtasks."""

from __future__ import annotations

import re
import time
from datetime import date

from app.agents.types import AgentTrace, Plan, Subtask

_YEAR_RE = re.compile(r"(20\d{2})\s*(?:年|年度)?")
_COMPARISON_TERMS = (
    "区别",
    "对比",
    "差异",
    "变化",
    "相比",
    "不同",
    "分别",
    "前后",
    "新旧",
    "哪些调整",
    "有何调整",
)
_MAX_COMPARISON_POINTS = 5


def _comparison_as_of(year: int, base_as_of: date) -> date:
    """Use a real policy date, not an arbitrary mid-year snapshot."""

    if year == base_as_of.year:
        return base_as_of
    return date(year, 12, 31)


class CoordinatorAgent:
    name = "coordinator"
    responsibility = "把问题拆成可按日期独立执行的任务，并调度其他智能体。"

    def plan(self, question: str, as_of: date | None = None) -> tuple[Plan, AgentTrace]:
        started = time.perf_counter()
        base_as_of = as_of or date.today()
        years = sorted({int(match) for match in _YEAR_RE.findall(question)})
        should_compare = len(years) >= 2 and any(term in question for term in _COMPARISON_TERMS)

        if should_compare:
            selected_years = years[:_MAX_COMPARISON_POINTS]
            subtasks = [
                Subtask(
                    id=f"year-{year}",
                    question=question,
                    as_of=_comparison_as_of(year, base_as_of),
                    label=f"{year} 年时点",
                    reason="历史/当前版本对比",
                )
                for year in selected_years
            ]
            plan = Plan(
                strategy="temporal_comparison",
                as_of=base_as_of,
                subtasks=subtasks,
                rationale=(
                    f"检测到 {len(selected_years)} 个时点及对比意图，按各年最后有效日期"
                    "（当前年用请求日期）分别检索，再由推理智能体合并。"
                ),
            )
            detail = f"拆分为 {len(subtasks)} 个时点子任务"
        else:
            plan = Plan(
                strategy="single_question",
                as_of=base_as_of,
                subtasks=[
                    Subtask(
                        id="main",
                        question=question,
                        as_of=base_as_of,
                        label="当前问题",
                        reason="单跳任务",
                    )
                ],
                rationale="未检测到多时点对比需求，交给单一检索任务处理。",
            )
            detail = "单一任务"

        trace = AgentTrace(
            agent=self.name,
            action="plan",
            status="completed",
            detail=detail,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            metadata={"strategy": plan.strategy, "years": years},
        )
        return plan, trace
