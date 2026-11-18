"""FastAPI application factory and local entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.evolver import EvolutionAgent
from app.agents.experience import ExperienceService
from app.agents.orchestrator import IntelligentOrganization
from app.api import chat, documents, health, organization, runs
from app.api import eval as eval_api
from app.config import Settings, get_settings
from app.db import Database
from app.evaluation.evaluator import Evaluator
from app.knowledge.embeddings import build_embeddings
from app.knowledge.retriever import Retriever
from app.rag.generator import build_generator
from app.rag.pipeline import RAGPipeline
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = (settings or get_settings()).resolve_paths()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_settings.eval_results_dir.mkdir(parents=True, exist_ok=True)

        database = Database(resolved_settings.database_path)
        database.initialize()
        embeddings = build_embeddings(
            resolved_settings.embedding_provider,
            resolved_settings.embedding_dimension,
        )
        retriever = Retriever(database, embeddings)
        generator = build_generator(resolved_settings)
        pipeline = RAGPipeline(retriever, generator, resolved_settings)
        experience_service = ExperienceService(database)
        organization_runtime = IntelligentOrganization(
            pipeline,
            experience_service,
            resolved_settings,
        )
        document_service = DocumentService(database, resolved_settings, embeddings)
        backfilled = document_service.backfill_fingerprints()
        if backfilled:
            logger.info("backfilled fingerprints for %s documents", backfilled)
        chat_service = ChatService(database, organization_runtime)
        evaluator = Evaluator(pipeline, resolved_settings)
        evolver = EvolutionAgent(pipeline, evaluator, resolved_settings)

        app.state.db = database
        app.state.embeddings = embeddings
        app.state.retriever = retriever
        app.state.pipeline = pipeline
        app.state.document_service = document_service
        app.state.chat_service = chat_service
        app.state.organization = organization_runtime
        app.state.experience_service = experience_service
        app.state.evaluator = evaluator
        app.state.evolver = evolver

        if resolved_settings.auto_ingest_sample_docs:
            result = document_service.seed_sample_documents()
            logger.info(
                "sample corpus sync: ingested=%s updated=%s skipped=%s",
                result.ingested,
                result.updated,
                result.skipped,
            )
            if result.errors:
                logger.warning("sample corpus skipped documents: %s", result.errors)
        logger.info(
            "RAG lab ready: db=%s provider=%s",
            resolved_settings.database_path,
            resolved_settings.generator_provider,
        )
        yield

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description=(
            "A reproducible enterprise RAG quality lab with hybrid retrieval, "
            "grounded citations, prompt-injection defenses and a fixed benchmark."
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(runs.router)
    app.include_router(eval_api.router)
    app.include_router(organization.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
