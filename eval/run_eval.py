#!/usr/bin/env python
"""Run the fixed benchmark and write a reproducible JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import build_settings
from app.db import Database
from app.evaluation.dataset import load_dataset
from app.evaluation.evaluator import Evaluator
from app.evaluation.report import (
    compare_reports,
    load_report,
    render_summary,
    write_report,
)
from app.knowledge.embeddings import build_embeddings
from app.knowledge.retriever import Retriever
from app.rag.generator import build_generator
from app.rag.pipeline import RAGPipeline
from app.services.document_service import DocumentService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the enterprise RAG benchmark.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "eval" / "dataset.jsonl",
        help="JSONL evaluation dataset.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "eval.db",
        help="Isolated SQLite database for the benchmark.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "eval" / "results" / "baseline.json",
        help="Destination JSON report.",
    )
    parser.add_argument("--name", default="baseline", help="Human-readable run name.")
    parser.add_argument("--top-k", type=int, default=None, help="Override retrieval depth.")
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Do not ingest data/sample_docs before evaluation.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Previous report to compare against.",
    )
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--fail-under-pass-rate", type=float, default=None)
    parser.add_argument("--fail-under-hit-rate", type=float, default=None)
    parser.add_argument(
        "--require-quality-gate",
        action="store_true",
        help="Fail when any built-in quality gate check fails.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = build_settings(
        database_path=args.database,
        sample_docs_dir=PROJECT_ROOT / "data" / "sample_docs",
        eval_dataset_path=args.dataset,
        eval_results_dir=PROJECT_ROOT / "eval" / "results",
    )
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(settings.database_path)
    database.initialize()
    embeddings = build_embeddings(settings.embedding_provider, settings.embedding_dimension)
    retriever = Retriever(database, embeddings)
    generator = build_generator(settings)
    pipeline = RAGPipeline(retriever, generator, settings)
    document_service = DocumentService(database, settings, embeddings)

    if not args.no_seed:
        seeded = document_service.seed_sample_documents()
        print(
            f"corpus: ingested={seeded.ingested} updated={seeded.updated} "
            f"skipped={seeded.skipped}",
            file=sys.stderr,
        )

    dataset = load_dataset(args.dataset)
    report = Evaluator(pipeline, settings).evaluate(
        dataset,
        top_k=args.top_k,
        name=args.name,
    )
    output_path = write_report(report, args.output)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(render_summary(report))
        print(f"report: {output_path}")

    if args.baseline:
        baseline = load_report(args.baseline)
        print("baseline comparison:")
        for row in compare_reports(report, baseline):
            print(f"  {row}")

    exit_code = 0
    if (
        args.fail_under_pass_rate is not None
        and report.metrics.pass_rate < args.fail_under_pass_rate
    ):
        print(
            f"quality gate failed: pass_rate {report.metrics.pass_rate:.3f} "
            f"< {args.fail_under_pass_rate:.3f}",
            file=sys.stderr,
        )
        exit_code = 1
    if (
        args.fail_under_hit_rate is not None
        and report.metrics.retrieval_hit_rate < args.fail_under_hit_rate
    ):
        print(
            f"quality gate failed: retrieval_hit_rate "
            f"{report.metrics.retrieval_hit_rate:.3f} < {args.fail_under_hit_rate:.3f}",
            file=sys.stderr,
        )
        exit_code = 1
    if args.require_quality_gate and not report.quality_gate.passed:
        failed = [name for name, passed in report.quality_gate.checks.items() if not passed]
        print(f"quality gate failed: {', '.join(failed)}", file=sys.stderr)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
