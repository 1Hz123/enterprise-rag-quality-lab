"""Evaluation dataset, benchmark execution and result endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.config import PROJECT_ROOT
from app.db import utc_now
from app.evaluation.dataset import load_dataset
from app.evaluation.report import EvalReport
from app.schemas import (
    DatasetPreview,
    EvalRunList,
    EvalRunRequest,
    EvalRunSummary,
)

router = APIRouter(prefix="/api/v1/eval", tags=["evaluation"])


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


def _load_json(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


@router.get("/dataset", response_model=DatasetPreview)
def dataset_preview(request: Request) -> DatasetPreview:
    settings = request.app.state.settings
    try:
        dataset = load_dataset(settings.eval_dataset_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"evaluation dataset is invalid: {exc}",
        ) from exc
    return DatasetPreview(
        name=dataset.name,
        path=_display_path(dataset.path),
        checksum=dataset.checksum,
        case_count=len(dataset.cases),
        categories=dataset.categories,
        cases=dataset.preview(),
    )


@router.post("/run", response_model=EvalReport)
def run_evaluation(payload: EvalRunRequest, request: Request) -> EvalReport:
    settings = request.app.state.settings
    try:
        dataset = load_dataset(settings.eval_dataset_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"evaluation dataset is invalid: {exc}",
        ) from exc

    report = request.app.state.evaluator.evaluate(
        dataset,
        top_k=payload.top_k,
        name=payload.name,
    )

    if payload.write_artifact:
        artifact = settings.eval_results_dir / f"{payload.name}-{report.run_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    db = request.app.state.db
    created_at = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO eval_runs
                (id, name, dataset_name, status, metrics_json, cases_json,
                 config_json, report_json, created_at, finished_at)
            VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
            """,
            (
                report.run_id,
                report.name,
                report.dataset_name,
                json.dumps(report.metrics.model_dump(mode="json"), ensure_ascii=False),
                json.dumps(
                    [case.model_dump(mode="json") for case in report.cases],
                    ensure_ascii=False,
                ),
                json.dumps(report.config, ensure_ascii=False),
                json.dumps(report.model_dump(mode="json"), ensure_ascii=False),
                created_at,
                report.generated_at,
            ),
        )
    return report


@router.get("/baseline", response_model=EvalReport)
def baseline_report(request: Request) -> EvalReport:
    settings = request.app.state.settings
    path = settings.eval_results_dir / "baseline.json"
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"baseline report not found: {_display_path(path)}",
        )
    return EvalReport.model_validate_json(path.read_text(encoding="utf-8"))


@router.get("/results", response_model=EvalRunList)
def list_eval_results(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> EvalRunList:
    db = request.app.state.db
    rows = db.fetch_all(
        "SELECT * FROM eval_runs ORDER BY created_at DESC, id LIMIT ?",
        (limit,),
    )
    total_row = db.fetch_one("SELECT COUNT(*) AS n FROM eval_runs")
    items: list[EvalRunSummary] = []
    for row in rows:
        metrics = _load_json(row["metrics_json"], {})
        if not isinstance(metrics, dict):
            metrics = {}
        items.append(
            EvalRunSummary(
                run_id=row["id"],
                name=row["name"],
                dataset_name=row["dataset_name"],
                status=row["status"],
                total_cases=int(metrics.get("total_cases", 0)),
                pass_rate=float(metrics.get("pass_rate", 0.0)),
                metrics=metrics,
                created_at=row["created_at"],
                finished_at=row["finished_at"],
            )
        )
    return EvalRunList(items=items, total=int(total_row["n"]) if total_row else 0)


@router.get("/results/{run_id}", response_model=EvalReport)
def get_eval_result(run_id: str, request: Request) -> EvalReport:
    row = request.app.state.db.fetch_one("SELECT * FROM eval_runs WHERE id = ?", (run_id,))
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"evaluation run not found: {run_id}"
        )
    payload = _load_json(row["report_json"], {})
    try:
        return EvalReport.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="stored evaluation report is invalid",
        ) from exc
