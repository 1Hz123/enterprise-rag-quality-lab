"""Controlled evaluation-driven evolution for the organization."""

from __future__ import annotations

import time

from app.config import Settings
from app.evaluation.dataset import load_dataset
from app.evaluation.evaluator import Evaluator
from app.rag.pipeline import RAGPipeline


class EvolutionAgent:
    """Search a small, bounded coverage grid and optionally promote the winner.

    This is deliberately a *lab* loop: candidates are evaluated on the same
    fixed benchmark, and the result is only a recommendation unless ``apply``
    is explicitly requested. A production system should require human approval
    and a held-out dataset before changing live thresholds.
    """

    name = "evolution"
    responsibility = "用固定评测集搜索更优阈值，形成可验证的受控进化建议。"

    CANDIDATES = (0.15, 0.20, 0.25, 0.30, 0.35)

    def __init__(self, pipeline: RAGPipeline, evaluator: Evaluator, settings: Settings) -> None:
        self.pipeline = pipeline
        self.evaluator = evaluator
        self.settings = settings

    def run(self, *, apply: bool = False) -> dict[str, object]:
        started = time.perf_counter()
        dataset = load_dataset(self.settings.eval_dataset_path)
        original = self.settings.min_query_coverage
        values = sorted({*self.CANDIDATES, original})
        candidates: list[dict[str, object]] = []
        baseline_metrics: dict[str, object] | None = None

        try:
            for value in values:
                self.settings.min_query_coverage = value
                report = self.evaluator.evaluate(
                    dataset,
                    name=f"evolution-{value:.2f}",
                )
                metrics = {
                    "min_query_coverage": value,
                    "pass_rate": round(report.metrics.pass_rate, 4),
                    "retrieval_hit_rate": round(report.metrics.retrieval_hit_rate, 4),
                    "citation_precision": round(report.metrics.citation_precision, 4),
                    "injection_resistance": round(report.metrics.injection_resistance, 4),
                    "p95_latency_ms": round(report.metrics.p95_latency_ms, 2),
                }
                candidates.append(metrics)
                if value == original:
                    baseline_metrics = metrics
        finally:
            self.settings.min_query_coverage = original

        assert baseline_metrics is not None
        best = max(
            candidates,
            key=lambda item: (
                float(item["pass_rate"]),
                float(item["citation_precision"]),
                -float(item["p95_latency_ms"]),
            ),
        )
        improved = (
            float(best["pass_rate"]) >= float(baseline_metrics["pass_rate"])
            and float(best["citation_precision"])
            >= float(baseline_metrics["citation_precision"])
            and float(best["injection_resistance"]) == 1.0
        )
        recommended = bool(improved and float(best["min_query_coverage"]) != original)
        applied = False
        if apply and recommended:
            self.settings.min_query_coverage = float(best["min_query_coverage"])
            applied = True

        return {
            "baseline": baseline_metrics,
            "candidates": candidates,
            "best": best,
            "recommended": recommended,
            "applied": applied,
            "applied_value": float(best["min_query_coverage"]) if applied else None,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "caveat": (
                "The grid is evaluated on the same fixed benchmark. "
                "Treat it as a regression experiment, not proof of generalization."
            ),
        }
