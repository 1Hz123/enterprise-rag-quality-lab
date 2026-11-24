"""Document ingestion, indexing and CRUD."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database, utc_now
from app.knowledge.embeddings import EmbeddingModel
from app.knowledge.loader import LoadedDocument, load_directory, normalize_text
from app.knowledge.splitter import estimate_tokens, split_document
from app.schemas import (
    DocumentCreate,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    DocumentVersionList,
    DocumentVersionOut,
    IngestResult,
)


class DocumentNotFoundError(LookupError):
    def __init__(self, document_id: str) -> None:
        super().__init__(f"document not found: {document_id}")
        self.document_id = document_id


class DuplicateDocumentError(ValueError):
    def __init__(self, document_id: str) -> None:
        super().__init__(f"an identical document already exists: {document_id}")
        self.document_id = document_id


class PolicyConflictError(ValueError):
    def __init__(self, document_id: str) -> None:
        super().__init__(
            "the requested policy effective window overlaps another version: "
            f"{document_id}"
        )
        self.document_id = document_id


logger = logging.getLogger(__name__)

_POLICY_METADATA_FIELDS = ("policy_key", "version", "effective_from", "effective_until")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


_VOLATILE_METADATA_KEYS = {
    "filename",
    "extension",
    "size_bytes",
    "encoding",
    "origin",
    "relative_path",
}


def document_identity(payload: DocumentCreate, content: str) -> str:
    """Fingerprint the document substance, not its transport filename.

    Same prose + same policy window is a duplicate even when uploaded under a
    different filename. Same prose with a different effective window is a new
    policy version and must be allowed to coexist.
    """

    metadata = {
        key: value
        for key, value in payload.metadata.items()
        if key not in _VOLATILE_METADATA_KEYS
    }
    body = {
        "content": content,
        "policy_key": payload.policy_key,
        "version": payload.version,
        "effective_from": (
            payload.effective_from.isoformat() if payload.effective_from else None
        ),
        "effective_until": (
            payload.effective_until.isoformat() if payload.effective_until else None
        ),
        "metadata": metadata,
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def policy_fields_from_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    """Extract version fields from a document frontmatter mapping."""

    fields: dict[str, str] = {}
    for key in _POLICY_METADATA_FIELDS:
        value = metadata.get(key)
        if value is None:
            continue
        cleaned = str(value).strip()
        if cleaned:
            fields[key] = cleaned
    return fields


def document_create_from_loaded(loaded: LoadedDocument) -> DocumentCreate:
    policy_fields = policy_fields_from_metadata(loaded.metadata)
    if loaded.effective_from:
        policy_fields.setdefault("effective_from", loaded.effective_from.isoformat())
    if loaded.effective_until:
        policy_fields.setdefault("effective_until", loaded.effective_until.isoformat())
    return DocumentCreate(
        title=loaded.title,
        content=loaded.content,
        source=loaded.source,
        content_type=loaded.content_type,
        metadata=loaded.metadata,
        **policy_fields,
    )


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class DocumentService:
    def __init__(self, db: Database, settings: Settings, embeddings: EmbeddingModel) -> None:
        self.db = db
        self.settings = settings
        self.embeddings = embeddings

    @staticmethod
    def _metadata(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _summary(self, row: sqlite3.Row) -> DocumentSummary:
        return DocumentSummary(
            id=row["id"],
            title=row["title"],
            source=row["source"],
            content_type=row["content_type"],
            checksum=row["checksum"],
            fingerprint=row["fingerprint"],
            policy_key=row["policy_key"],
            version=row["version"],
            effective_from=_as_date(row["effective_from"]),
            effective_until=_as_date(row["effective_until"]),
            status=row["status"],
            chunk_count=int(row["chunk_count"]),
            metadata=self._metadata(row),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _detail(self, row: sqlite3.Row) -> DocumentDetail:
        return DocumentDetail(
            **self._summary(row).model_dump(),
            content=row["content"],
        )

    def _build_chunk_rows(
        self,
        document_id: str,
        loaded: LoadedDocument,
        now: str,
    ) -> list[tuple[Any, ...]]:
        chunks = split_document(
            loaded,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        if not chunks:
            raise ValueError("document produced no indexable chunks")

        embedding_inputs = [
            f"{loaded.title}\n{chunk.section or ''}\n{chunk.text}".strip() for chunk in chunks
        ]
        vectors = self.embeddings.embed(embedding_inputs)
        return [
            (
                f"{document_id}:{chunk.ordinal:04d}",
                document_id,
                chunk.ordinal,
                chunk.section,
                chunk.text,
                estimate_tokens(chunk.text),
                _json(vector),
                _json(
                    {
                        **chunk.metadata,
                        "embedding_model": self.embeddings.name,
                        "embedding_dimension": self.embeddings.dimension,
                    }
                ),
                now,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

    @staticmethod
    def _find_policy_conflict(
        connection: sqlite3.Connection,
        *,
        policy_key: str | None,
        effective_from: date | None,
        effective_until: date | None,
        exclude_id: str | None = None,
    ) -> str | None:
        if not policy_key:
            return None

        rows = connection.execute(
            """
            SELECT id, effective_from, effective_until
            FROM documents
            WHERE policy_key = ? AND id != ?
            """,
            (policy_key, exclude_id or ""),
        ).fetchall()
        new_start = effective_from or date.min
        new_end = effective_until or date.max
        for row in rows:
            existing_start = _as_date(row["effective_from"]) or date.min
            existing_end = _as_date(row["effective_until"]) or date.max
            if new_start <= existing_end and existing_start <= new_end:
                return str(row["id"])
        return None

    @staticmethod
    def _metadata_with_policy(payload: DocumentCreate) -> dict[str, Any]:
        metadata = dict(payload.metadata)
        for field_name in _POLICY_METADATA_FIELDS:
            value = getattr(payload, field_name)
            if value is not None and value != "":
                metadata[field_name] = value.isoformat() if isinstance(value, date) else value
        return metadata

    def _loaded_for_payload(
        self,
        payload: DocumentCreate,
        *,
        content: str,
        source: str | None = None,
    ) -> LoadedDocument:
        return LoadedDocument(
            title=payload.title.strip(),
            source=source or payload.source or "",
            content=content,
            content_type=payload.content_type,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            metadata=self._metadata_with_policy(payload),
        )

    @staticmethod
    def _record_version(connection: sqlite3.Connection, row: sqlite3.Row, now: str) -> None:
        loaded = LoadedDocument(
            title=row["title"],
            source=row["source"],
            content=row["content"],
            content_type=row["content_type"],
            effective_from=_as_date(row["effective_from"]),
            effective_until=_as_date(row["effective_until"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
        payload = document_create_from_loaded(loaded)
        fingerprint = row["fingerprint"] or document_identity(payload, loaded.content)
        connection.execute(
            """
            INSERT OR IGNORE INTO document_versions
                (id, document_id, version, policy_key, effective_from, effective_until,
                 content, checksum, fingerprint, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ver_{uuid.uuid4().hex}",
                row["id"],
                row["version"],
                row["policy_key"],
                row["effective_from"],
                row["effective_until"],
                row["content"],
                row["checksum"],
                fingerprint,
                row["metadata_json"],
                row["updated_at"] or now,
            ),
        )

    def create_document(self, payload: DocumentCreate) -> DocumentDetail:
        content = normalize_text(payload.content)
        if not content:
            raise ValueError("document is empty after normalization")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.settings.max_document_bytes:
            raise ValueError(f"document exceeds {self.settings.max_document_bytes} bytes")

        title = payload.title.strip()
        document_id = f"doc_{uuid.uuid4().hex}"
        source = payload.source or f"manual/{document_id}.md"
        checksum = hashlib.sha256(content_bytes).hexdigest()
        loaded = self._loaded_for_payload(payload, content=content, source=source)
        fingerprint = document_identity(payload, content)
        now = utc_now()

        with self.db.connect() as connection:
            duplicate = connection.execute(
                "SELECT id FROM documents WHERE fingerprint = ? LIMIT 1", (fingerprint,)
            ).fetchone()
            if duplicate:
                raise DuplicateDocumentError(duplicate["id"])

            conflict = self._find_policy_conflict(
                connection,
                policy_key=payload.policy_key,
                effective_from=payload.effective_from,
                effective_until=payload.effective_until,
            )
            if conflict:
                raise PolicyConflictError(conflict)

            chunk_rows = self._build_chunk_rows(document_id, loaded, now)
            connection.execute(
                """
                INSERT INTO documents
                    (id, title, source, content_type, content, checksum, fingerprint,
                     policy_key, version, effective_from, effective_until,
                     metadata_json, status, chunk_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed', ?, ?, ?)
                """,
                (
                    document_id,
                    title,
                    source,
                    payload.content_type,
                    content,
                    checksum,
                    fingerprint,
                    payload.policy_key,
                    payload.version,
                    payload.effective_from.isoformat() if payload.effective_from else None,
                    payload.effective_until.isoformat() if payload.effective_until else None,
                    _json(self._metadata_with_policy(payload)),
                    len(chunk_rows),
                    now,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO chunks
                    (id, document_id, ordinal, section, content, token_estimate,
                     embedding_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                chunk_rows,
            )
            created_row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if created_row:
                self._record_version(connection, created_row, now)

        return self.get_document(document_id)

    def update_from_loaded(self, document_id: str, loaded: LoadedDocument) -> DocumentDetail:
        payload = document_create_from_loaded(loaded)
        content = normalize_text(payload.content)
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.settings.max_document_bytes:
            raise ValueError(f"document exceeds {self.settings.max_document_bytes} bytes")
        checksum = hashlib.sha256(content_bytes).hexdigest()
        now = utc_now()

        with self.db.connect() as connection:
            current = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not current:
                raise DocumentNotFoundError(document_id)

            updated_loaded = LoadedDocument(
                title=payload.title,
                source=payload.source,
                content=content,
                content_type=payload.content_type,
                effective_from=payload.effective_from,
                effective_until=payload.effective_until,
                metadata=self._metadata_with_policy(payload),
            )
            fingerprint = document_identity(payload, content)
            duplicate = connection.execute(
                "SELECT id FROM documents WHERE fingerprint = ? AND id != ? LIMIT 1",
                (fingerprint, document_id),
            ).fetchone()
            if duplicate:
                raise DuplicateDocumentError(duplicate["id"])

            conflict = self._find_policy_conflict(
                connection,
                policy_key=payload.policy_key,
                effective_from=payload.effective_from,
                effective_until=payload.effective_until,
                exclude_id=document_id,
            )
            if conflict:
                raise PolicyConflictError(conflict)

            self._record_version(connection, current, now)
            chunk_rows = self._build_chunk_rows(document_id, updated_loaded, now)
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            connection.execute(
                """
                UPDATE documents
                SET title = ?, source = ?, content_type = ?, content = ?, checksum = ?,
                    fingerprint = ?, policy_key = ?, version = ?, effective_from = ?,
                    effective_until = ?, metadata_json = ?, status = 'indexed',
                    chunk_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.title,
                    payload.source,
                    payload.content_type,
                    content,
                    checksum,
                    fingerprint,
                    payload.policy_key,
                    payload.version,
                    payload.effective_from.isoformat() if payload.effective_from else None,
                    payload.effective_until.isoformat() if payload.effective_until else None,
                    _json(self._metadata_with_policy(payload)),
                    len(chunk_rows),
                    now,
                    document_id,
                ),
            )
            connection.executemany(
                """
                INSERT INTO chunks
                    (id, document_id, ordinal, section, content, token_estimate,
                     embedding_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                chunk_rows,
            )
            updated_row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if updated_row:
                self._record_version(connection, updated_row, now)

        return self.get_document(document_id)

    def get_document(self, document_id: str) -> DocumentDetail:
        row = self.db.fetch_one("SELECT * FROM documents WHERE id = ?", (document_id,))
        if not row:
            raise DocumentNotFoundError(document_id)
        return self._detail(row)

    def find_by_source(self, source: str) -> DocumentDetail | None:
        row = self.db.fetch_one("SELECT * FROM documents WHERE source = ? LIMIT 1", (source,))
        return self._detail(row) if row else None

    def list_documents(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        as_of: date | None = None,
    ) -> DocumentList:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        if as_of:
            as_of_value = as_of.isoformat()
            rows = self.db.fetch_all(
                """
                SELECT *
                FROM documents
                WHERE (effective_from IS NULL OR effective_from <= ?)
                  AND (effective_until IS NULL OR effective_until >= ?)
                ORDER BY updated_at DESC, id
                LIMIT ? OFFSET ?
                """,
                (as_of_value, as_of_value, limit, offset),
            )
            total_row = self.db.fetch_one(
                """
                SELECT COUNT(*) AS n
                FROM documents
                WHERE (effective_from IS NULL OR effective_from <= ?)
                  AND (effective_until IS NULL OR effective_until >= ?)
                """,
                (as_of_value, as_of_value),
            )
        else:
            rows = self.db.fetch_all(
                "SELECT * FROM documents ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
                (limit, offset),
            )
            total_row = self.db.fetch_one("SELECT COUNT(*) AS n FROM documents")
        return DocumentList(
            items=[self._summary(row) for row in rows],
            total=int(total_row["n"]) if total_row else 0,
            limit=limit,
            offset=offset,
        )

    def list_versions(self, document_id: str) -> DocumentVersionList:
        if not self.db.fetch_one("SELECT id FROM documents WHERE id = ?", (document_id,)):
            raise DocumentNotFoundError(document_id)
        rows = self.db.fetch_all(
            """
            SELECT *
            FROM document_versions
            WHERE document_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (document_id,),
        )
        items = [
            DocumentVersionOut(
                id=row["id"],
                document_id=row["document_id"],
                version=row["version"],
                policy_key=row["policy_key"],
                effective_from=_as_date(row["effective_from"]),
                effective_until=_as_date(row["effective_until"]),
                checksum=row["checksum"],
                fingerprint=row["fingerprint"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
        return DocumentVersionList(items=items, total=len(items))

    def backfill_fingerprints(self) -> int:
        """Compute fingerprints for rows created before the v1.1 schema."""

        rows = self.db.fetch_all("SELECT * FROM documents WHERE fingerprint IS NULL")
        updated = 0
        for row in rows:
            loaded = LoadedDocument(
                title=row["title"],
                source=row["source"],
                content=row["content"],
                content_type=row["content_type"],
                effective_from=_as_date(row["effective_from"]),
                effective_until=_as_date(row["effective_until"]),
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            payload = document_create_from_loaded(loaded)
            try:
                with self.db.connect() as connection:
                    connection.execute(
                        "UPDATE documents SET fingerprint = ? WHERE id = ?",
                        (document_identity(payload, loaded.content), row["id"]),
                    )
            except sqlite3.IntegrityError:
                logger.warning("duplicate fingerprint while backfilling document %s", row["id"])
                continue
            updated += 1
        return updated

    def delete_document(self, document_id: str) -> None:
        with self.db.connect() as connection:
            cursor = connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            if cursor.rowcount == 0:
                raise DocumentNotFoundError(document_id)

    def reindex_document(self, document_id: str) -> DocumentDetail:
        detail = self.get_document(document_id)
        now = utc_now()
        with self.db.connect() as connection:
            loaded = LoadedDocument(
                title=detail.title,
                source=detail.source,
                content=detail.content,
                content_type=detail.content_type,
                effective_from=detail.effective_from,
                effective_until=detail.effective_until,
                metadata=detail.metadata,
            )
            chunk_rows = self._build_chunk_rows(document_id, loaded, now)
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            connection.executemany(
                """
                INSERT INTO chunks
                    (id, document_id, ordinal, section, content, token_estimate,
                     embedding_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                chunk_rows,
            )
            connection.execute(
                """
                UPDATE documents
                SET chunk_count = ?, status = 'indexed', updated_at = ?
                WHERE id = ?
                """,
                (len(chunk_rows), now, document_id),
            )
        return self.get_document(document_id)

    def reindex_all(self) -> int:
        rows = self.db.fetch_all("SELECT id FROM documents ORDER BY id")
        for row in rows:
            self.reindex_document(row["id"])
        return len(rows)

    def ingest_directory(self, directory: str | Path) -> IngestResult:
        loaded_documents = load_directory(
            directory,
            max_bytes=self.settings.max_document_bytes,
            max_pdf_pages=self.settings.max_pdf_pages,
        )
        ingested = 0
        updated = 0
        skipped = 0
        errors: list[str] = []
        touched: list[DocumentSummary] = []

        for loaded in loaded_documents:
            existing = self.find_by_source(loaded.source)
            payload = document_create_from_loaded(loaded)
            fingerprint = document_identity(payload, loaded.content)
            try:
                if existing is None:
                    created = self.create_document(payload)
                    touched.append(DocumentSummary(**created.model_dump(exclude={"content"})))
                    ingested += 1
                elif existing.fingerprint != fingerprint:
                    refreshed = self.update_from_loaded(existing.id, loaded)
                    touched.append(DocumentSummary(**refreshed.model_dump(exclude={"content"})))
                    updated += 1
                else:
                    skipped += 1
            except DuplicateDocumentError as exc:
                message = f"{loaded.source}: {exc}"
                logger.warning("skipping duplicate document: %s", message)
                errors.append(message)
                skipped += 1

        return IngestResult(
            ingested=ingested,
            updated=updated,
            skipped=skipped,
            documents=touched,
            errors=errors,
        )

    def seed_sample_documents(self) -> IngestResult:
        if not self.settings.sample_docs_dir.is_dir():
            return IngestResult(ingested=0, updated=0, skipped=0, documents=[])
        return self.ingest_directory(self.settings.sample_docs_dir)
