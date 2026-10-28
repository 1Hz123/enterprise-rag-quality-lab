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

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def tokenize(text: str, *, include_unigrams: bool = True) -> list[str]:
    """Tokenize Latin words and Chinese character bigrams.

    Chinese unigrams are included for the embedding model but excluded from
    lexical scoring, where bigrams carry far more signal.
    """

    normalized = normalize_text(text)
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
    if len(left) != len(right) or not left:
        return 0.0
    score = sum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, score))


class EmbeddingModel(Protocol):
    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbeddings:
    """Signed hashing embeddings with L2 normalization."""

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 32:
            raise ValueError("dimension must be at least 32")
        self.dimension = dimension
        self.name = f"hashing-{dimension}"

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
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def build_embeddings(provider: str = "hashing", dimension: int = 384) -> EmbeddingModel:
    if provider == "hashing":
        return HashingEmbeddings(dimension=dimension)
    raise ValueError(f"unsupported embedding provider: {provider}")
