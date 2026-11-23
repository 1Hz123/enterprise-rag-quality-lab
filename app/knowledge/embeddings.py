"""Deterministic local embeddings.

A signed feature-hashing model over Chinese character n-grams and Latin words.
It is not meant to rival a hosted embedding model; it makes the lab runnable
offline and provides a stable baseline that can be replaced through the small
embedding interface below.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from app.core.cache import CacheManager, CacheRequest, pack_embedding, unpack_embedding
from app.knowledge.splitter import estimate_tokens

_TOKEN_RE = re.compile(
    r"\d+(?:[._+-]\d+)*[\u3400-\u4dbf\u4e00-\u9fff]?"
    r"|[a-z0-9]+(?:[._+-][a-z0-9]+)*"
    r"|[\u3400-\u4dbf\u4e00-\u9fff]+"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_UNIT_GAP_RE = re.compile(
    r"(?<=\d)\s+(?=(?:个工作日|个月|年|天|个|小时|分钟|周|月|元|%))"
)


def normalize_for_retrieval(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return _UNIT_GAP_RE.sub("", normalized)


def tokenize(text: str, *, include_unigrams: bool = True) -> list[str]:
    """Tokenize Latin words and Chinese character bigrams.

    Numeric-unit tokens such as ``3年`` are preserved, while a Latin word is
    not allowed to swallow the first character of a following Chinese word.
    Chinese unigrams are included for the embedding model but excluded from
    lexical scoring, where bigrams carry far more signal.
    """

    normalized = normalize_for_retrieval(text)
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        value = match.group(0)
        if _CJK_RE.match(value):
            characters = list(value)
            if include_unigrams:
                tokens.extend(characters)
            tokens.extend(
                characters[index] + characters[index + 1]
                for index in range(len(characters) - 1)
            )
        elif len(value) > 1:
            tokens.append(value)
    return tokens


def lexical_tokens(text: str) -> list[str]:
    """Return high-signal tokens for BM25 and sentence scoring."""

    return [token for token in tokenize(text, include_unigrams=False) if len(token) > 1]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        raise ValueError("embedding vectors must not be empty")
    if len(left) != len(right):
        raise ValueError(
            f"embedding dimension mismatch: left={len(left)}, right={len(right)}; "
            "reindex the corpus after changing embedding_dimension"
        )
    score = sum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, score))


class EmbeddingModel(Protocol):
    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbeddings:
    """Signed hashing embeddings with L2 normalization."""

    def __init__(
        self,
        dimension: int = 384,
        *,
        cache: CacheManager | None = None,
    ) -> None:
        if dimension < 32:
            raise ValueError("dimension must be at least 32")
        self.dimension = dimension
        self.name = f"hashing-{dimension}"
        self.cache = cache

    def _cache_key(self, text: str) -> str:
        assert self.cache is not None
        return self.cache.key(
            "embedding",
            {
                "embedding_schema": 1,
                "model": self.name,
                "dimension": self.dimension,
                "text": normalize_for_retrieval(text),
            },
        )

    def _encode(self, text: str) -> bytes:
        return pack_embedding(self._embed(text), dimension=self.dimension)

    @staticmethod
    def _token_weight(token: str) -> float:
        return 0.35 if len(token) == 1 else 1.0

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        counts = Counter(tokenize(text))
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            weight = (1.0 + math.log(count)) * self._token_weight(token)
            vector[index] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.cache is None or not self.cache.enabled:
            return [self._embed(text) for text in texts]

        requests = [
            CacheRequest(
                key=self._cache_key(text),
                loader=lambda text=text: self._encode(text),
                saved_input_tokens=estimate_tokens(text),
                saved_model_calls=1,
            )
            for text in texts
        ]
        cached = self.cache.get_many_or_load(
            "embedding",
            requests,
            ttl=self.cache.ttl("embedding"),
        )
        vectors: list[list[float]] = []
        for text in texts:
            read = cached.get(self._cache_key(text))
            if read is None or read.value is None:
                vectors.append(self._embed(text))
                continue
            try:
                vectors.append(
                    unpack_embedding(read.value, expected_dimension=self.dimension)
                )
            except ValueError:
                # A stale/corrupt value must behave like a miss, never like a
                # wrong vector. The next write repairs it.
                vectors.append(self._embed(text))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


def build_embeddings(
    provider: str = "hashing",
    dimension: int = 384,
    *,
    cache: CacheManager | None = None,
) -> EmbeddingModel:
    if provider == "hashing":
        return HashingEmbeddings(dimension=dimension, cache=cache)
    raise ValueError(f"unsupported embedding provider: {provider}")
