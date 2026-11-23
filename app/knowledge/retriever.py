"""Hybrid dense + BM25 retrieval over locally stored chunks."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.cache import CacheManager, CacheRequest
from app.db import Database
from app.knowledge.embeddings import EmbeddingModel, cosine_similarity, lexical_tokens


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    title: str
    source: str
    ordinal: int
    section: str | None
    policy_key: str | None
    version: str | None
    effective_from: str | None
    effective_until: str | None
    content: str
    score: float
    vector_score: float
    lexical_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.title,
            "source": self.source,
            "ordinal": self.ordinal,
            "section": self.section,
            "policy_key": self.policy_key,
            "version": self.version,
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "content": self.content,
            "score": self.score,
            "vector_score": self.vector_score,
            "lexical_score": self.lexical_score,
        }


class Retriever:
    """Score every chunk for a query and return the best ``top_k``.

    The corpus is intentionally small (an enterprise policy lab, not a web
    search engine), so a transparent in-process scan is preferable to an
    approximate index. The scoring contract is stable and easy to replace.
    """

    def __init__(
        self,
        db: Database,
        embeddings: EmbeddingModel,
        *,
        cache: CacheManager | None = None,
    ) -> None:
        self.db = db
        self.embeddings = embeddings
        self.cache = cache

    def index_version(self) -> str:
        """A cheap manifest hash; any write/reindex changes retrieval keys."""

        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(MAX(updated_at), '') AS latest,
                   COALESCE(SUM(chunk_count), 0) AS chunks,
                   COALESCE(MAX(checksum), '') AS checksum
            FROM documents
            """
        )
        manifest = "|".join(
            (
                str(row["n"]) if row else "0",
                str(row["latest"]) if row else "",
                str(row["chunks"]) if row else "0",
                str(row["checksum"]) if row else "",
            )
        )
        return hashlib.sha256(manifest.encode("utf-8")).hexdigest()

    def _load_rows(
        self,
        document_ids: list[str] | None,
        as_of: date | None,
    ) -> list[Any]:
        sql = """
            SELECT c.id AS chunk_id, c.document_id, c.ordinal, c.section,
                   c.content, c.embedding_json, d.title, d.source,
                   d.policy_key, d.version, d.effective_from, d.effective_until
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.status = 'indexed'
        """
        params: list[Any] = []
        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            sql += f" AND c.document_id IN ({placeholders})"
            params.extend(document_ids)
        if as_of:
            sql += (
                " AND (d.effective_from IS NULL OR d.effective_from <= ?)"
                " AND (d.effective_until IS NULL OR d.effective_until >= ?)"
            )
            params.extend([as_of.isoformat(), as_of.isoformat()])
        return self.db.fetch_all(sql, tuple(params))

    @staticmethod
    def _bm25_scores(
        query_tokens: list[str],
        documents: list[list[str]],
    ) -> list[float]:
        if not query_tokens or not documents:
            return [0.0] * len(documents)

        total_documents = len(documents)
        average_length = sum(len(tokens) for tokens in documents) / total_documents or 1.0
        document_frequencies = Counter()
        for tokens in documents:
            document_frequencies.update(set(tokens))

        idf: dict[str, float] = {}
        for token in set(query_tokens):
            frequency = document_frequencies.get(token, 0)
            idf[token] = math.log(1.0 + (total_documents - frequency + 0.5) / (frequency + 0.5))

        k1 = 1.5
        b = 0.75
        scores: list[float] = []
        for tokens in documents:
            term_frequencies = Counter(tokens)
            length = len(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = term_frequencies.get(token, 0)
                if not frequency:
                    continue
                denominator = frequency + k1 * (1.0 - b + b * length / average_length)
                score += idf[token] * frequency * (k1 + 1.0) / denominator
            scores.append(score)
        return scores

    def _serialize_results(self, chunks: list[RetrievedChunk]) -> bytes:
        return json.dumps(
            {
                "schema_version": 1,
                "items": [chunk.to_dict() for chunk in chunks],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _deserialize_results(self, payload: bytes) -> list[RetrievedChunk]:
        data = json.loads(payload.decode("utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("unsupported retrieval cache schema")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("retrieval cache payload has no items")
        return [RetrievedChunk(**item) for item in items]

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        document_ids: list[str] | None = None,
        as_of: date | None = None,
    ) -> list[RetrievedChunk]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")

        if self.cache is None or not self.cache.enabled:
            return self._retrieve_uncached(
                query,
                top_k=top_k,
                document_ids=document_ids,
                as_of=as_of,
            )

        index_version = self.index_version()
        ids = sorted(document_ids) if document_ids else []
        key = self.cache.key(
            "retrieval",
            {
                "retrieval_schema": 1,
                "query": query,
                "top_k": top_k,
                "document_ids": ids,
                "as_of": as_of.isoformat() if as_of else None,
                "index_version": index_version,
                "embedding_model": self.embeddings.name,
                "embedding_dimension": self.embeddings.dimension,
                "scope": self.cache.settings.cache_scope,
            },
            index_version=index_version,
        )

        def load() -> bytes:
            return self._serialize_results(
                self._retrieve_uncached(
                    query,
                    top_k=top_k,
                    document_ids=document_ids,
                    as_of=as_of,
                )
            )

        def is_empty(payload: bytes) -> bool:
            try:
                return not bool(json.loads(payload.decode("utf-8")).get("items"))
            except (TypeError, ValueError, json.JSONDecodeError):
                return False

        empty_ttl = (
            self.cache.ttl("retrieval", empty=True)
            if self.cache.settings.negative_cache_enabled
            else None
        )
        values = self.cache.get_many_or_load(
            "retrieval",
            [CacheRequest(key=key, loader=load, saved_vector_calls=1)],
            ttl=self.cache.ttl("retrieval"),
            empty_check=is_empty,
            empty_ttl=empty_ttl,
        )
        read = values.get(key)
        if read is None or read.value is None:
            return self._retrieve_uncached(
                query,
                top_k=top_k,
                document_ids=document_ids,
                as_of=as_of,
            )
        try:
            return self._deserialize_results(read.value)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.cache.delete("retrieval", key)
            return self._retrieve_uncached(
                query,
                top_k=top_k,
                document_ids=document_ids,
                as_of=as_of,
            )

    def _retrieve_uncached(
        self,
        query: str,
        *,
        top_k: int,
        document_ids: list[str] | None = None,
        as_of: date | None = None,
    ) -> list[RetrievedChunk]:
        rows = self._load_rows(document_ids, as_of)
        if not rows:
            return []

        query_vector = self.embeddings.embed_query(query)
        query_tokens = list(dict.fromkeys(lexical_tokens(query)))
        token_lists = [lexical_tokens(row["content"]) for row in rows]
        bm25 = self._bm25_scores(query_tokens, token_lists)

        scored: list[RetrievedChunk] = []
        for row, lexical_raw in zip(rows, bm25, strict=True):
            try:
                chunk_vector = json.loads(row["embedding_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            vector_score = max(0.0, cosine_similarity(query_vector, chunk_vector))

            lexical_score = lexical_raw / (lexical_raw + 3.0) if lexical_raw else 0.0
            title_tokens = set(lexical_tokens(row["title"]))
            if title_tokens and query_tokens:
                title_coverage = len(title_tokens.intersection(query_tokens)) / len(query_tokens)
            else:
                title_coverage = 0.0

            score = 0.55 * vector_score + 0.40 * lexical_score + 0.05 * title_coverage
            scored.append(
                RetrievedChunk(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    title=row["title"],
                    source=row["source"],
                    ordinal=int(row["ordinal"]),
                    section=row["section"],
                    policy_key=row["policy_key"],
                    version=row["version"],
                    effective_from=row["effective_from"],
                    effective_until=row["effective_until"],
                    content=row["content"],
                    score=round(min(1.0, score), 6),
                    vector_score=round(vector_score, 6),
                    lexical_score=round(lexical_score, 6),
                )
            )

        scored.sort(key=lambda chunk: (-chunk.score, chunk.chunk_id))
        return scored[:top_k]
