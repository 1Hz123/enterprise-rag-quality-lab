"""Query normalization shared by retrieval and the agent team."""

from __future__ import annotations

import re

from app.knowledge.embeddings import normalize_for_retrieval

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_NUMBER_RE = re.compile(
    r"(?P<number>[零〇一二两三四五六七八九十百]+)"
    r"(?P<unit>个工作日|个月|年|天|个|小时|分钟|周|次|人|元)"
)


def _cn_to_int(value: str) -> int:
    if "百" in value:
        hundreds, _, remainder = value.partition("百")
        base = (_CN_DIGITS.get(hundreds, 1) if hundreds else 1) * 100
        return base + (_cn_to_int(remainder) if remainder else 0)
    if "十" in value:
        tens, _, ones = value.partition("十")
        tens_value = _CN_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CN_DIGITS.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    total = 0
    for character in value:
        if character not in _CN_DIGITS:
            return total
        total = total * 10 + _CN_DIGITS[character]
    return total


def _replace_chinese_number(match: re.Match[str]) -> str:
    raw = match.group("number")
    if not raw:
        return match.group(0)
    return f"{_cn_to_int(raw)}{match.group('unit')}"


def normalize_query(query: str) -> str:
    """Normalize full-width characters, units and Chinese numerals.

    Examples: ``三年`` -> ``3年``; ``三个工作日`` -> ``3个工作日``;
    ``3 年`` -> ``3年``. This keeps lexical and vector tokens aligned with
    documents that use Arabic numerals.
    """

    normalized = normalize_for_retrieval(query)
    normalized = _CN_NUMBER_RE.sub(_replace_chinese_number, normalized)
    return re.sub(r"\s+", " ", normalized).strip()
