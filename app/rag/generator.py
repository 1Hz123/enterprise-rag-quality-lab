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
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from app.config import Settings
from app.knowledge.embeddings import lexical_tokens
from app.rag.intent import article_terms, focus_tokens, question_aspects
from app.rag.prompt import (
    NO_CONTEXT_ANSWER,
    ContextItem,
    build_messages,
)

_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.!?])\s+|\n+")
_BULLET_RE = re.compile(r"^\s{0,3}(?:[-*+•]|\d+[.)])\s+")
_CONTROVERSY_RE = re.compile(r"^(?:备注[:：])?争议[。.!?]?$")
_METADATA_RE = re.compile(
    r"^(标题[:：]|来源[:：]|发布日期[:：]|发文字号[:：]|（此件公开发布）|"
    r"中华人民共和国国务院令|国务院办公厅关于开展$|第[一二三四五六七八九十百零〇两\d]+号$|"
    r".*的通知[。.]?$)"
)
_PREAMBLE_RE = re.compile(
    r"^(现将|国务院关于印发|.*印发给你们|.*已经.*会议|.*现予公布|.*此件公开发布)"
)
_TOC_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、|\d+(?:\.\d+)*\s+)[^。]{0,40}[。.]?$"
)
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_REFERENCE_RE = re.compile(r"https?://|doi\.org|\bdoi\b|^来源[:：]|^参考文献")
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
    quotes: dict[int, str] = field(default_factory=dict)
    fallback_used: bool = False


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
        if len(sentence) < 6:
            continue
        if set(sentence) <= set("|-: "):
            # Markdown table separator, not a content row.
            continue
        sentences.append(sentence)

    # “备注：争议。” is a heading-like sentence. Keep the immediately following
    # explanation with it, otherwise the extractive answer says only “争议”
    # without saying what the controversy is.
    merged: list[str] = []
    index = 0
    while index < len(sentences):
        sentence = sentences[index]
        if _CONTROVERSY_RE.match(sentence) and index + 1 < len(sentences):
            merged.append(f"{sentence} {sentences[index + 1]}")
            index += 2
        else:
            merged.append(sentence)
            index += 1
    return merged


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

    article_bonus = 0.0
    for article in article_terms(question):
        if sentence.lstrip().startswith(article):
            article_bonus = 0.50
            break
        if article in sentence:
            article_bonus = max(article_bonus, 0.15)

    policy_intent_bonus = 0.0
    if any(term in question for term in ("主要目标", "目标是什么", "到2030年")) and (
        "到2030年" in sentence or "目标" in sentence
    ):
        policy_intent_bonus = 0.25
    if any(term in question for term in ("主要部署", "哪些任务", "重点任务")):
        if re.match(r"^[（(]?[一二三四五六七八九十\d]+[）)、.]", sentence.strip()):
            policy_intent_bonus = 0.55
        elif "任务" in sentence or "工作要求" in sentence:
            policy_intent_bonus = 0.30

    return (
        0.45 * coverage
        + 0.23 * density
        + 0.25 * item.chunk.score
        + 0.05 * title_coverage
        + _numeric_signal(question, sentence)
        + article_bonus
        + policy_intent_bonus
    )


def _ensure_terminal_punctuation(sentence: str) -> str:
    stripped = sentence.strip()
    if stripped and stripped[-1] not in "。！？!?；;：:":
        return f"{stripped}。"
    return stripped


class ExtractiveGenerator:
    """Select grounded source sentences and attach citation markers."""

    mode = "extractive"

    def __init__(self, max_answer_chars: int = 1200, max_sentences: int = 5) -> None:
        self.max_answer_chars = max_answer_chars
        self.max_sentences = max_sentences
        self.cache_signature = (
            f"extractive:v10:max_answer_chars={max_answer_chars}:"
            f"max_sentences={max_sentences}"
        )

    def generate(self, question: str, items: list[ContextItem]) -> Generation:
        candidates: list[tuple[float, ContextItem, str]] = []
        seen: set[str] = set()
        aspects = question_aspects(question)
        query_focus = set(focus_tokens(question))
        articles = article_terms(question)

        for item in items:
            if articles and not any(article in item.text for article in articles):
                continue
            for sentence in _sentences(item.text):
                normalized = re.sub(r"\s+", "", sentence)
                if (
                    normalized in seen
                    or _REFERENCE_RE.search(sentence)
                    or _METADATA_RE.match(sentence.strip())
                    or _PREAMBLE_RE.match(sentence.strip())
                    or _TOC_RE.match(sentence.strip())
                    or sentence.strip() == (item.chunk.title or "").strip()
                ):
                    continue
                seen.add(normalized)
                score = _sentence_score(question, sentence, item)
                sentence_tokens = set(lexical_tokens(sentence))
                covers_aspect = any(aspect.intersection(sentence_tokens) for aspect in aspects)
                focus_hit = bool(query_focus.intersection(sentence_tokens))
                if covers_aspect or (score >= 0.12 and (focus_hit or not query_focus)):
                    candidates.append((score, item, sentence))

        if not candidates:
            return Generation(text=NO_CONTEXT_ANSWER, used_indices=[], refused=True)

        candidates.sort(key=lambda candidate: (-candidate[0], candidate[1].index))
        selected: list[tuple[float, ContextItem, str]] = []
        covered_aspects: set[int] = set()

        def new_aspects(candidate: tuple[float, ContextItem, str]) -> set[int]:
            tokens = set(lexical_tokens(candidate[2]))
            return {
                index
                for index, aspect in enumerate(aspects)
                if aspect.intersection(tokens) and index not in covered_aspects
            }

        limit = self.max_sentences if len(aspects) > 1 else min(3, self.max_sentences)
        primary_index = items[0].index if items else candidates[0][1].index
        primary_pool = [
            candidate for candidate in candidates if candidate[1].index == primary_index
        ]

        def covers_all_aspects(pool: list[tuple[float, ContextItem, str]]) -> bool:
            if not aspects:
                return True
            pool_tokens = [set(lexical_tokens(candidate[2])) for candidate in pool]
            return all(
                any(aspect.intersection(tokens) for tokens in pool_tokens)
                for aspect in aspects
            )

        # If the primary chunk covers every facet, do not cite a second source
        # merely to pad the answer; that was the main citation-precision leak.
        conclusion_in_primary = "结论" in question and any(
            "结论" in candidate[2] for candidate in primary_pool
        )
        latin_focus = {token for token in query_focus if re.search(r"[a-zA-Z]", token)}
        primary_focus_ok = not latin_focus or any(
            any(token.lower() in candidate[2].lower() for token in latin_focus)
            for candidate in primary_pool
        )
        remaining = list(
            primary_pool
            if (
                covers_all_aspects(primary_pool) or conclusion_in_primary
            )
            and primary_focus_ok
            else candidates
        )
        while remaining and len(selected) < limit:
            if selected:
                # Prefer the top-ranked context item when it already covers an
                # aspect; this keeps PDF page/section bleed out of the answer.
                best = max(
                    remaining,
                    key=lambda candidate: (
                        len(new_aspects(candidate)),
                        -candidate[1].index,
                        candidate[0],
                    ),
                )
            else:
                best = remaining[0]
            selected.append(best)
            covered_aspects.update(new_aspects(best))
            remaining.remove(best)

        used_indices: set[int] = set()
        quotes: dict[int, str] = {}
        parts: list[str] = []
        total_chars = 0
        for _, item, sentence in selected:
            rendered = f"{_ensure_terminal_punctuation(sentence)} [{item.index}]"
            if parts and total_chars + len(rendered) > self.max_answer_chars:
                break
            parts.append(rendered)
            used_indices.add(item.index)
            quotes.setdefault(item.index, sentence)
            total_chars += len(rendered)

        if not parts:
            return Generation(text=NO_CONTEXT_ANSWER, used_indices=[], refused=True)

        answer = "根据知识库资料：" + " ".join(parts)
        if len(answer) > self.max_answer_chars:
            answer = answer[: self.max_answer_chars].rstrip() + "…"
        return Generation(text=answer, used_indices=sorted(used_indices), quotes=quotes)


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
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise GenerationError("OPENAI_BASE_URL must be an http(s) URL")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_answer_chars = max_answer_chars
        self.mode = f"openai:{model}"
        self.cache_signature = (
            f"openai:{model}:temperature=0.0:max_tokens=600:"
            f"max_answer_chars={max_answer_chars}"
        )

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

        last_error: Exception | None = None
        body: dict[str, object] | None = None
        for attempt in range(2):
            try:
                # Scheme and host are validated in __init__; this is an
                # operator-configured endpoint, not user input.
                with urllib.request.urlopen(  # nosec B310
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
        if body is None:
            error_name = type(last_error).__name__ if last_error else "unknown"
            raise GenerationError(f"generator request failed: {error_name}") from last_error

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


class FallbackGenerator:
    """Retry remote generation, then fall back to the deterministic generator."""

    def __init__(self, primary: Generator, fallback: Generator) -> None:
        self.primary = primary
        self.fallback = fallback
        self.mode = f"{primary.mode}+extractive"
        self.cache_signature = (
            f"fallback:{getattr(primary, 'cache_signature', primary.mode)}|"
            f"{getattr(fallback, 'cache_signature', fallback.mode)}"
        )

    def generate(self, question: str, items: list[ContextItem]) -> Generation:
        try:
            return self.primary.generate(question, items)
        except GenerationError:
            generation = self.fallback.generate(question, items)
            return Generation(
                text=generation.text,
                used_indices=generation.used_indices,
                refused=generation.refused,
                quotes=generation.quotes,
                fallback_used=True,
            )


def build_generator(settings: Settings) -> Generator:
    if settings.generator_provider == "extractive":
        return ExtractiveGenerator(max_answer_chars=settings.max_answer_chars)
    if settings.generator_provider == "openai":
        if settings.openai_api_key is None:
            raise GenerationError("OPENAI_API_KEY is required for the openai generator")
        primary = OpenAICompatibleGenerator(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_answer_chars=settings.max_answer_chars,
        )
        fallback = ExtractiveGenerator(max_answer_chars=settings.max_answer_chars)
        return FallbackGenerator(primary, fallback)
    raise GenerationError(f"unsupported generator provider: {settings.generator_provider}")
