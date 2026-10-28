"""Question-answering endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.rag.generator import GenerationError
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    try:
        return request.app.state.chat_service.chat(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="the configured answer generator is unavailable",
        ) from exc
