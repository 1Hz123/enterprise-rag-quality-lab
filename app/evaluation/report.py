"""Serializable evaluation report and human-readable summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.2"


class EvalMetrics(BaseModel):
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    answerable_cases: int
    answerable_passed: int
    retrieval_hit_rate: float
    mrr: float
    keyword_recall: float
    citation_precision: float
    invalid_citation_rate: float = 0.0
    invalid_citation_count: int = 0
    invalid_citation_cases: int = 0
    cache_hit_cases: int = 0
    cache_hit_rate: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_errors: int = 0
    cache_saved_ms: float = 0.0
    refusal_accuracy: float
    injection_resistance: float
    p50_latency_ms: float
    p95_latency_ms: float
    avg_latency_ms: float
    security_blocks: int


class QualityGate(BaseModel):
    passed: bool
    checks: dict[str, bool]
    thresholds: dict[str, float]


class CaseResult(BaseModel):
    case_id: str
    category: str
    question: str
    behavior: str
    passed: bool
    expected_sources: list[str]
    expected_keywords: list[str]
    retrieved_sources: list[str]
    cited_sources: list[str]
    retrieval_hit: bool
    mrr: float
    keyword_recall: float | None = None
    citation_precision: float | None = None
    invalid_citation_rate: float = 0.0
    invalid_citation_count: int = 0
    cache_hit: bool = False
    cache_layers: list[str] = Field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    cache_errors: int = 0
    cache_saved_ms: float = 0.0
    raw_answer_preview: str = ""
    refused: bool
    security_blocked: bool
    latency_ms: float
    answer_preview: str
    failure_reasons: list[str] = Field(default_factory=list)
    error: str | None = None


class EvalReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    name: str
    dataset_name: str
    dataset_path: str
    dataset_checksum: str
    generated_at: str
    duration_ms: float
    config: dict[str, Any]
    metrics: EvalMetrics
    category_pass_rates: dict[str, float]
    quality_gate: QualityGate
    cases: list[CaseResult]


def write_report(report: EvalReport, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_report(path: str | Path) -> EvalReport:
    return EvalReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def render_summary(report: EvalReport) -> str:
    metrics = report.metrics
    lines = [
        f"RAG quality report | {report.name} | {report.dataset_name}",
        f"Run: {report.run_id}",
        (
            f"Pass {metrics.passed_cases}/{metrics.total_cases} "
            f"({metrics.pass_rate:.1%}) | "
            f"hit@k {metrics.retrieval_hit_rate:.1%} | "
            f"MRR {metrics.mrr:.3f} | "
            f"keywords {metrics.keyword_recall:.1%}"
        ),
        (
            f"Citation precision {metrics.citation_precision:.1%} | "
            f"invalid citations {metrics.invalid_citation_rate:.1%} | "
            f"refusal {metrics.refusal_accuracy:.1%} | "
            f"injection resistance {metrics.injection_resistance:.1%}"
        ),
        (
            f"Latency p50 {metrics.p50_latency_ms:.1f} ms | "
            f"p95 {metrics.p95_latency_ms:.1f} ms"
        ),
    ]
    if bool(report.config.get("cache_enabled")):
        lines.append(
            f"Cache hit samples {metrics.cache_hit_cases}/{metrics.total_cases} "
            f"({metrics.cache_hit_rate:.1%}); hits={metrics.cache_hits} "
            f"misses={metrics.cache_misses} errors={metrics.cache_errors}"
        )
    lines.append(f"Quality gate: {'PASS' if report.quality_gate.passed else 'FAIL'}")
    failed = [case for case in report.cases if not case.passed]
    if failed:
        lines.append("Failures:")
        for case in failed:
            reasons = "; ".join(case.failure_reasons) or "unknown"
            lines.append(f"  - {case.case_id} [{case.category}] {reasons}")
    return "\n".join(lines)


def compare_reports(current: EvalReport, baseline: EvalReport) -> list[str]:
    """Return human-readable deltas against a committed baseline."""

    rows: list[str] = []
    fields = (
        ("pass_rate", "pass rate"),
        ("retrieval_hit_rate", "retrieval hit"),
        ("mrr", "MRR"),
        ("keyword_recall", "keyword recall"),
        ("citation_precision", "citation precision"),
        ("invalid_citation_rate", "invalid citation rate"),
        ("injection_resistance", "injection resistance"),
        ("p95_latency_ms", "p95 latency (ms)"),
    )
    for field, label in fields:
        current_value = getattr(current.metrics, field)
        baseline_value = getattr(baseline.metrics, field)
        delta = current_value - baseline_value
        if field.endswith("_ms"):
            rows.append(f"{label}: {baseline_value:.1f} -> {current_value:.1f} ({delta:+.1f})")
        else:
            rows.append(
                f"{label}: {baseline_value:.1%} -> {current_value:.1%} "
                f"({delta * 100:+.1f} pp)"
            )
    return rows
