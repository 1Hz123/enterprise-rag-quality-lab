"""Experience library: turn audited answers into reusable organizational knowledge."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.agents.types import ExperienceRecord
from app.db import Database, utc_now
from app.knowledge.retriever import RetrievedChunk
from app.rag.pipeline import Citation, RagResult
from app.rag.query import normalize_query

logger = logging.getLogger(__name__)

EXPERIENCE_TTL_DAYS = 30


class ExperienceNotFoundError(LookupError):
    def __init__(self, experience_id: str) -> None:
        super().__init__(f"experience not found: {experience_id}")
        self.experience_id = experience_id


class RunFeedbackNotFoundError(LookupError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run not found: {run_id}")
        self.run_id = run_id


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _normalize_question(question: str) -> str:
    normalized = normalize_query(question)
    return re.sub(r"[\s，。？！、,.?!:：;；\-_/]+", "", normalized)


class ExperienceService:
    """High-quality Q&A memory with guarded, context-hash-checked reuse."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def _context_hash(self, citations: list[Citation], signature: str = "") -> str | None:
        chunk_ids = [citation.chunk_id for citation in citations]
        if not chunk_ids:
            return None
        rows = self.db.fetch_all(
            """
            SELECT id, content
            FROM chunks
            WHERE id IN (SELECT value FROM json_each(?))
            """,
            (_json(chunk_ids),),
        )
        if len(rows) != len(set(chunk_ids)):
            return None
        digest = hashlib.sha256()
        digest.update(signature.encode("utf-8"))
        digest.update(b"\x00")
        for row in sorted(rows, key=lambda item: item["id"]):
            digest.update(str(row["id"]).encode())
            digest.update(b"\x00")
            digest.update(str(row["content"]).encode())
        return digest.hexdigest()

    @staticmethod
    def _row_record(row: Any) -> ExperienceRecord:
        citations = [
            Citation(**citation)
            for citation in _load_json(row["citations_json"], [])
            if isinstance(citation, dict)
        ]
        retrieved = [
            RetrievedChunk(**chunk)
            for chunk in _load_json(row["retrieved_json"], [])
            if isinstance(chunk, dict)
        ]
        return ExperienceRecord(
            id=row["id"],
            question=row["question"],
            normalized_question=row["normalized_question"],
            as_of=date.fromisoformat(row["as_of"]),
            answer=row["answer"],
            citations=citations,
            retrieved=retrieved,
            quality_score=float(row["quality_score"]),
            uses=int(row["uses"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )

    def save_experience(
        self,
        question: str,
        as_of: date,
        result: RagResult,
        *,
        quality_score: float,
        signature: str = "",
    ) -> ExperienceRecord | None:
        if result.refused or not result.citations:
            return None
        context_hash = self._context_hash(result.citations, signature)
        if not context_hash:
            return None

        normalized = _normalize_question(question)
        experience_id = f"exp_{uuid.uuid4().hex}"
        now = utc_now()
        citations = [asdict(citation) for citation in result.citations]
        retrieved = [chunk.to_dict() for chunk in result.retrieved]
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO experiences
                    (id, question, normalized_question, as_of, answer, citations_json,
                     retrieved_json, quality_score, context_hash, signature, uses, created_at,
                     last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)
                ON CONFLICT(normalized_question, as_of) DO UPDATE SET
                    question = excluded.question,
                    answer = excluded.answer,
                    citations_json = excluded.citations_json,
                    retrieved_json = excluded.retrieved_json,
                    quality_score = excluded.quality_score,
                    context_hash = excluded.context_hash,
                    signature = excluded.signature,
                    created_at = excluded.created_at,
                    last_used_at = NULL
                """,
                (
                    experience_id,
                    question,
                    normalized,
                    as_of.isoformat(),
                    result.answer,
                    _json(citations),
                    _json(retrieved),
                    quality_score,
                    context_hash,
                    signature,
                    now,
                ),
            )
        row = self.db.fetch_one(
            "SELECT * FROM experiences WHERE normalized_question = ? AND as_of = ?",
            (normalized, as_of.isoformat()),
        )
        return self._row_record(row) if row else None

    @staticmethod
    def _citations_cover(citations: list[Citation], as_of: date) -> bool:
        for citation in citations:
            try:
                if citation.effective_from and date.fromisoformat(citation.effective_from) > as_of:
                    return False
                if (
                    citation.effective_until
                    and date.fromisoformat(citation.effective_until) < as_of
                ):
                    return False
            except ValueError:
                return False
        return bool(citations)

    @staticmethod
    def _is_expired(created_at: str) -> bool:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return created < datetime.now(UTC) - timedelta(days=EXPERIENCE_TTL_DAYS)

    def _safe_delete(self, experience_id: str) -> None:
        try:
            self.delete_experience(experience_id)
        except ExperienceNotFoundError:
            logger.debug("experience already removed: %s", experience_id)
        except Exception:
            logger.exception("failed to delete stale experience: %s", experience_id)

    def find_reusable(
        self,
        question: str,
        as_of: date,
        *,
        signature: str = "",
    ) -> ExperienceRecord | None:
        normalized = _normalize_question(question)
        rows = self.db.fetch_all(
            """
            SELECT *
            FROM experiences
            WHERE normalized_question = ?
            ORDER BY created_at DESC
            """,
            (normalized,),
        )
        for row in rows:
            record = self._row_record(row)
            if self._is_expired(record.created_at):
                self._safe_delete(record.id)
                continue
            if row["signature"] != signature:
                continue
            if not self._citations_cover(record.citations, as_of):
                continue
            if self._context_hash(record.citations, signature) != row["context_hash"]:
                # The source document changed after this experience was captured.
                self._safe_delete(record.id)
                continue

            now = utc_now()
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE experiences
                    SET uses = uses + 1, last_used_at = ?
                    WHERE id = ?
                    """,
                    (now, record.id),
                )
            refreshed = self.db.fetch_one(
                "SELECT * FROM experiences WHERE id = ?", (record.id,)
            )
            return self._row_record(refreshed) if refreshed else None
        return None

    def list_experiences(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ExperienceRecord], int]:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        rows = self.db.fetch_all(
            "SELECT * FROM experiences ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        total_row = self.db.fetch_one("SELECT COUNT(*) AS n FROM experiences")
        return (
            [self._row_record(row) for row in rows],
            int(total_row["n"]) if total_row else 0,
        )

    def stats(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(uses), 0) AS uses,
                   COALESCE(AVG(quality_score), 0) AS avg_quality
            FROM experiences
            """
        )
        return {
            "total": int(row["total"]) if row else 0,
            "uses": int(row["uses"]) if row else 0,
            "avg_quality": round(float(row["avg_quality"]), 4) if row else 0.0,
        }

    def delete_experience(self, experience_id: str) -> None:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM experiences WHERE id = ?",
                (experience_id,),
            )
            if cursor.rowcount == 0:
                raise ExperienceNotFoundError(experience_id)

    def record_feedback(
        self,
        run_id: str,
        *,
        rating: int,
        comment: str | None = None,
        corrected_answer: str | None = None,
    ) -> dict[str, Any]:
        run = self.db.fetch_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not run:
            raise RunFeedbackNotFoundError(run_id)

        feedback_id = f"fb_{uuid.uuid4().hex}"
        now = utc_now()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback
                    (id, run_id, rating, comment, corrected_answer, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (feedback_id, run_id, rating, comment, corrected_answer, now),
            )

        metrics = _load_json(run["metrics_json"], {})
        as_of_raw = metrics.get("as_of") if isinstance(metrics, dict) else None
        as_of = date.fromisoformat(as_of_raw) if isinstance(as_of_raw, str) else date.today()
        normalized = _normalize_question(run["question"])
        action = "recorded"
        experience_id: str | None = None

        plan_strategy = metrics.get("plan_strategy") if isinstance(metrics, dict) else None

        if rating <= 2:
            with self.db.connect() as connection:
                connection.execute(
                    "DELETE FROM experiences WHERE normalized_question = ? AND as_of = ?",
                    (normalized, as_of.isoformat()),
                )
            action = "suppressed"
        elif rating >= 4 and not bool(run["refused"]) and plan_strategy == "single_question":
            citations = [
                Citation(**citation)
                for citation in _load_json(run["citations_json"], [])
                if isinstance(citation, dict)
            ]
            retrieved = [
                RetrievedChunk(**chunk)
                for chunk in _load_json(run["retrieval_json"], [])
                if isinstance(chunk, dict)
            ]
            quality = (
                float(metrics.get("quality_score", 0.85))
                if isinstance(metrics, dict)
                else 0.85
            )
            result = RagResult(
                answer=run["answer"],
                refused=False,
                security_blocked=False,
                as_of=as_of,
                citations=citations,
                retrieved=retrieved,
                latency_ms=float(run["latency_ms"]),
                prompt_version=run["prompt_version"],
                mode=run["mode"],
                metrics=metrics if isinstance(metrics, dict) else {},
            )
            record = self.save_experience(
                run["question"],
                as_of,
                result,
                quality_score=max(0.85, quality),
            )
            experience_id = record.id if record else None
            action = "promoted" if record else "recorded"

        return {
            "feedback_id": feedback_id,
            "run_id": run_id,
            "rating": rating,
            "action": action,
            "experience_id": experience_id,
        }
