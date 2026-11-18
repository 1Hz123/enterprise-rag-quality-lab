"""Document ingestion and CRUD endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status

from app.knowledge.loader import DocumentLoadError, load_bytes
from app.schemas import (
    DocumentCreate,
    DocumentDetail,
    DocumentList,
    DocumentVersionList,
    IngestResult,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    PolicyConflictError,
    document_create_from_loaded,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _duplicate_detail(exc: DuplicateDocumentError) -> dict[str, str]:
    return {
        "message": "an identical document already exists",
        "document_id": exc.document_id,
    }


def _policy_conflict_detail(exc: PolicyConflictError) -> dict[str, str]:
    return {
        "message": "policy effective window overlaps an existing version",
        "document_id": exc.document_id,
    }


@router.post("", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, request: Request) -> DocumentDetail:
    try:
        return request.app.state.document_service.create_document(payload)
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_detail(exc),
        ) from exc
    except PolicyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_policy_conflict_detail(exc),
        ) from exc
    except (ValueError, DocumentLoadError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/upload", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(..., description="UTF-8 .md or .txt document"),
) -> DocumentDetail:
    settings = request.app.state.settings
    data = await file.read(settings.max_document_bytes + 1)
    try:
        loaded = load_bytes(
            file.filename or "upload.txt",
            data,
            content_type=file.content_type,
            max_bytes=settings.max_document_bytes,
        )
        payload = document_create_from_loaded(loaded)
        return request.app.state.document_service.create_document(payload)
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_detail(exc),
        ) from exc
    except PolicyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_policy_conflict_detail(exc),
        ) from exc
    except (ValueError, DocumentLoadError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await file.close()


@router.post("/ingest-sample", response_model=IngestResult)
def ingest_sample_documents(request: Request) -> IngestResult:
    try:
        return request.app.state.document_service.seed_sample_documents()
    except PolicyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_policy_conflict_detail(exc),
        ) from exc
    except (ValueError, DocumentLoadError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=DocumentList)
def list_documents(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    as_of: date | None = Query(
        default=None,
        description="Only return documents effective on this date.",
    ),
) -> DocumentList:
    return request.app.state.document_service.list_documents(
        limit=limit,
        offset=offset,
        as_of=as_of,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, request: Request) -> DocumentDetail:
    try:
        return request.app.state.document_service.get_document(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{document_id}/versions", response_model=DocumentVersionList)
def list_document_versions(document_id: str, request: Request) -> DocumentVersionList:
    try:
        return request.app.state.document_service.list_versions(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{document_id}/reindex", response_model=DocumentDetail)
def reindex_document(document_id: str, request: Request) -> DocumentDetail:
    try:
        return request.app.state.document_service.reindex_document(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str, request: Request) -> None:
    try:
        request.app.state.document_service.delete_document(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
