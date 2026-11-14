"""Inspection and memory endpoints for the intelligent organization."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.agents.experience import ExperienceNotFoundError
from app.schemas import (
    AgentProfile,
    EvolutionCandidate,
    EvolutionReport,
    EvolutionRequest,
    ExperienceList,
    ExperienceOut,
    OrganizationStatus,
)

router = APIRouter(prefix="/api/v1/organization", tags=["organization"])


@router.get("", response_model=OrganizationStatus)
def organization_status(request: Request) -> OrganizationStatus:
    runtime = request.app.state.organization
    stats = request.app.state.experience_service.stats()
    return OrganizationStatus(
        name="Intelligent Organization RAG",
        principle="任务型协作 + 经验沉淀 + 审计闭环 + 评测驱动",
        agents=[AgentProfile(**agent) for agent in runtime.roster],
        experience_total=stats["total"],
        experience_uses=stats["uses"],
        average_quality=stats["avg_quality"],
        experience_reuse_enabled=request.app.state.settings.experience_reuse_enabled,
        loop=[
            "Coordinator 拆解任务",
            "Retriever 收集带时效的证据",
            "Reasoner 生成或对比答案",
            "Auditor 校验引用、时效与安全",
            "Experience 沉淀高分问答",
            "Feedback / CI 驱动下一轮改进",
        ],
    )


@router.get("/experiences", response_model=ExperienceList)
def list_experiences(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ExperienceList:
    records, total = request.app.state.experience_service.list_experiences(
        limit=limit,
        offset=offset,
    )
    return ExperienceList(
        items=[ExperienceOut(**record.to_public()) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/experiences/{experience_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experience(experience_id: str, request: Request) -> None:
    try:
        request.app.state.experience_service.delete_experience(experience_id)
    except ExperienceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/evolve", response_model=EvolutionReport)
def evolve(payload: EvolutionRequest, request: Request) -> EvolutionReport:
    result = request.app.state.evolver.run(apply=payload.apply)
    return EvolutionReport(
        baseline=EvolutionCandidate(**result["baseline"]),
        candidates=[EvolutionCandidate(**candidate) for candidate in result["candidates"]],
        best=EvolutionCandidate(**result["best"]),
        recommended=bool(result["recommended"]),
        applied=bool(result["applied"]),
        applied_value=result["applied_value"],
        duration_ms=float(result["duration_ms"]),
        caveat=str(result["caveat"]),
    )
