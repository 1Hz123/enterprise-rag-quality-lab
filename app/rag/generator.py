"""Answer generators.

``ExtractiveGenerator`` is the default and keeps the lab fully offline.
``OpenAICompatibleGenerator`` is optional and uses the exact same context and
citation contract, making model comparisons straightforward.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.knowledge.embeddings import lexical_tokens
from app.rag.prompt import (
    NO_CONTEXT_ANSWER,
    ContextItem,
    build_messages,
)

_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_BULLET_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_NUMERIC_INTENT_TERMS = (
    "多少",
    "几天",
    "多久",
    "时限",
    "期限",
    "标准",
    "时间",
    "比例",
    "金额",
    "日期",
    "几号",
    "几点",
    "上限",
    "下限",
)


class GenerationError(RuntimeError):
    """The configured generator failed to produce an answer."""


@dataclass(frozen=True, slots=True)
class Generation:
    text: str
    used_indices: list[int]
    refused: bool = False


class Generator(Protocol):
    mode: str

    def generate(self, question: str, items: list[ContextItem]) -> Generation: ...


def _sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for raw in _SENTENCE_RE.split(text):
        sentence = raw.strip()
        if not sentence or sentence.startswith("#"):
            continue
        sentence = _BULLET_RE.sub("", sentence).strip()
        if len(sentence) < 6 or sentence.startswith("|"):
            continue
        sentences.append(sentence)
    return sentences


def _numeric_signal(question: str, sentence: str) -> float:
    asks_for_number = any(term in question for term in _NUMERIC_INTENT_TERMS)
    has_number = bool(re.search(r"\d", sentence))
    return 0.12 if asks_for_number and has_number else 0.0


def _sentence_score(question: str, sentence: str, item: ContextItem) -> float:
    query_tokens = set(lexical_tokens(question))
    sentence_tokens = set(lexical_tokens(sentence))
    if not query_tokens or not sentence_tokens:
        return 0.0

    matched = query_tokens.intersection(sentence_tokens)
    coverage = len(matched) / len(query_tokens)
    density = len(matched) / math.sqrt(len(sentence_tokens) + 1)
    title_tokens = set(lexical_tokens(item.chunk.title))
    title_coverage = len(query_tokens.intersection(title_tokens)) / len(query_tokens)

    return (
        0.45 * coverage
        + 0.23 * density
        + 0.25 * item.chunk.score
        + 0.05 * title_coverage
        + _numeric_signal(question, sentence)
    )


def _ensure_terminal_punctuation(sentence: str) -> str:
    stripped = sentence.strip()
    if stripped and stripped[-1] not in "。！？!?；;：:":
        return f"{stripped}。"
    return stripped


class ExtractiveGenerator:
    """Select grounded source sentences and attach citation markers."""

    mode = "extractive"

    def __init__(self, max_answer_chars: int = 1200) -> None:
        self.max_answer_chars = max_answer_chars

    def generate(self, question: str, items: list[ContextItem]) -> Generation:
        candidates: list[tuple[float, ContextItem, str]] = []
        seen: set[str] = set()

        for item in items:
            for sentence in _sentences(item.text):
                normalized = re.sub(r"\s+", "", sentence)
                if normalized in seen:
                    continue
                seen.add(normalized)
                score = _sentence_score(question, sentence, item)
                if score >= 0.12:
                    candidates.append((score, item, sentence))

        if not candidates:
            return Generation(text=NO_CONTEXT_ANSWER, used_indices=[], refused=True)

        candidates.sort(key=lambda candidate: (-candidate[0], candidate[1].index))
        selected: list[tuple[float, ContextItem, str]] = []
        query_tokens = set(lexical_tokens(question))

        # Always keep the strongest sentence. A second source is only allowed
        # when it contributes at least one query token that the primary source
        # does not contain at all; this prevents unrelated documents from being
        # cited merely because they share a generic term such as "工作日".
        primary = candidates[0]
        primary_source = primary[1].chunk.source
        primary_source_tokens: set[str] = set()
        for item in items:
            if item.chunk.source == primary_source:
                primary_source_tokens.update(lexical_tokens(item.text))

        selected.append(primary)
        selected_sources = {primary_source}

        second_source_options: list[tuple[int, float, tuple[float, ContextItem, str]]] = []
        for candidate in candidates[1:]:
            score, item, sentence = candidate
            if item.chunk.source == primary_source:
                continue
            novel_tokens = (query_tokens & set(lexical_tokens(sentence))) - primary_source_tokens
            if len(novel_tokens) >= 2 and score >= primary[0] * 0.60:
                second_source_options.append((len(novel_tokens), score, candidate))
        if second_source_options:
            second_source_options.sort(key=lambda option: (-option[0], -option[1]))
            chosen = second_source_options[0][2]
            selected.append(chosen)
            selected_sources.add(chosen[1].chunk.source)

        for candidate in candidates:
            if len(selected) >= 3:
                break
            if candidate in selected:
                continue
            if candidate[1].chunk.source in selected_sources:
                selected.append(candidate)

        used_indices: set[int] = set()
        parts: list[str] = []
        total_chars = 0
        for _, item, sentence in selected:
            rendered = f"{_ensure_terminal_punctuation(sentence)} [{item.index}]"
            if parts and total_chars + len(rendered) > self.max_answer_chars:
                break
            parts.append(rendered)
            used_indices.add(item.index)
            total_chars += len(rendered)

        if not parts:
            return Generation(text=NO_CONTEXT_ANSWER, used_indices=[], refused=True)

        answer = "根据知识库资料：" + " ".join(parts)
        if len(answer) > self.max_answer_chars:
            answer = answer[: self.max_answer_chars].rstrip() + "…"
        return Generation(text=answer, used_indices=sorted(used_indices))


class OpenAICompatibleGenerator:
    """Minimal OpenAI-compatible chat-completions client (no SDK required)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 45.0,
        max_answer_chars: int = 1200,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_answer_chars = max_answer_chars
        self.mode = f"openai:{model}"

    def generate(self, question: str, items: list[ContextItem]) -> Generation:
        messages = build_messages(question, items)
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 600,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GenerationError(f"generator request failed: {type(exc).__name__}") from exc

        try:
            text = body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise GenerationError("generator returned an unexpected response shape") from exc

        if not text:
            raise GenerationError("generator returned an empty answer")

        text = text[: self.max_answer_chars]
        valid_indices = sorted(
            {
                int(match)
                for match in _CITATION_RE.findall(text)
                if 1 <= int(match) <= len(items)
            }
        )
        refused = NO_CONTEXT_ANSWER in text or "无法确定" in text
        return Generation(text=text, used_indices=valid_indices, refused=refused)


def build_generator(settings: Settings) -> Generator:
    if settings.generator_provider == "extractive":
        return ExtractiveGenerator(max_answer_chars=settings.max_answer_chars)
    if settings.generator_provider == "openai":
        if settings.openai_api_key is None:
            raise GenerationError("OPENAI_API_KEY is required for the openai generator")
        return OpenAICompatibleGenerator(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_answer_chars=settings.max_answer_chars,
        )
    raise GenerationError(f"unsupported generator provider: {settings.generator_provider}")
