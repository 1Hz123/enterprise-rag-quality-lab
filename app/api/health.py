"""Liveness, readiness and service metadata endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from app.schemas import HealthResponse

router = APIRouter(tags=["operations"])


@router.get("/", include_in_schema=False)
def root() -> dict[str, object]:
    return {
        "name": "Enterprise RAG Quality Lab",
        "docs": "/docs",
        "health": "/health",
        "api": {
            "documents": "/api/v1/documents",
            "chat": "/api/v1/chat",
            "runs": "/api/v1/runs",
            "evaluation": "/api/v1/eval",
        },
    }


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    database = request.app.state.db
    try:
        database.ping()
        counts = database.counts()
        database_status = "ok"
    except sqlite3.Error:
        counts = {"documents": 0, "chunks": 0, "runs": 0}
        database_status = "error"

    cache_health = request.app.state.cache.health()
    try:
        cache_health["index_version"] = request.app.state.retriever.index_version()
    except Exception:
        cache_health["index_version"] = None
    overall_ok = database_status == "ok" and cache_health["status"] not in {
        "degraded",
        "cooldown",
    }
    return HealthResponse(
        status="ok" if overall_ok else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        database=database_status,
        documents=counts["documents"],
        chunks=counts["chunks"],
        runs=counts["runs"],
        generator=request.app.state.pipeline.generator.mode,
        embedding=f"{settings.embedding_provider}-{settings.embedding_dimension}",
        cache=cache_health,
    )
