"""Inspect stored RAG runs and their retrieval traces."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.agents.experience import RunFeedbackNotFoundError
from app.schemas import FeedbackRequest, FeedbackResponse, RunDetail, RunList
from app.services.chat_service import RunNotFoundError

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("", response_model=RunList)
def list_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RunList:
    return request.app.state.chat_service.list_runs(limit=limit, offset=offset)


@router.post("/{run_id}/feedback", response_model=FeedbackResponse)
def submit_feedback(
    run_id: str,
    payload: FeedbackRequest,
    request: Request,
) -> FeedbackResponse:
    try:
        result = request.app.state.experience_service.record_feedback(
            run_id,
            rating=payload.rating,
            comment=payload.comment,
            corrected_answer=payload.corrected_answer,
        )
    except RunFeedbackNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return FeedbackResponse(**result)


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, request: Request) -> RunDetail:
    try:
        return request.app.state.chat_service.get_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
