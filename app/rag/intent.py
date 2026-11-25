"""Question intent helpers: focus terms and ambiguity guards."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.knowledge.embeddings import lexical_tokens

# A question about "假期" without a concrete leave type is not answerable with
# a single number: annual leave depends on tenure, sick leave is not a fixed
# number of days, and different policies have different rules.
LEAVE_TYPES = (
    "年假",
    "病假",
    "婚假",
    "产假",
    "陪产假",
    "丧假",
    "事假",
    "调休",
    "工伤假",
    "育儿假",
    "护理假",
    "探亲假",
    "法定节假日",
    "公休",
)
_GENERIC_LEAVE_TERMS = ("假期", "休假", "请假")
_QUANTITY_TERMS = ("多少天", "几天", "多久", "多少", "天数", "多长时间")
_ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千万零〇两\d]+条")
_TITLE_RE = re.compile(r"《([^》]{4,})》")
_POLICY_GENERIC_TERMS = frozenset(
    {
        "国务",
        "务院",
        "院办",
        "办公",
        "公厅",
        "厅关",
        "关于",
        "于加",
        "加强",
        "强中",
        "中小",
        "小企",
        "业回",
        "款难",
        "难问",
        "题治",
        "治理",
        "关工",
        "工作",
        "通知",
        "主要",
        "要部",
        "部署",
        "些任",
        "任务",
        "规定",
        "有关",
        "意见",
        "于进",
        "进一",
        "一步",
        "步加",
    }
)
# Bigrams crossing question particles (e.g. "有什", "少人", "盐那") are
# retrieval noise, not question subjects.
_FOCUS_NOISE_CHARS = frozenset("的了吗呢吧啊是有这那哪什么多少几目前")

# Generic words that make almost any policy chunk look relevant. They should
# not count as evidence that the answer addresses the question's subject.
_FOCUS_STOPWORDS = frozenset(
    {
        "公司",
        "员工",
        "本人",
        "我们",
        "企业",
        "单位",
        "制度",
        "规定",
        "政策",
        "标准",
        "流程",
        "如何",
        "怎么",
        "怎样",
        "什么",
        "哪些",
        "哪个",
        "多少",
        "几天",
        "多久",
        "天数",
        "时间",
        "期限",
        "需要",
        "必须",
        "可以",
        "是否",
        "以及",
        "目前",
        "现在",
        "请问",
        "一下",
        "相关",
        "具体",
        "问题",
        "情况",
        "一个",
        "这个",
        "那个",
        "还有",
        "分别",
        "区别",
        "对比",
        "差异",
        "变化",
        "为准",
        "执行",
    }
)

CLARIFICATION_ANSWER = (
    "“假期”是一个宽泛说法，不能用一个固定天数回答。请先明确假期类型："
    "年假、病假、婚假、产假/陪产假、丧假、事假、调休等。"
    "不同假期的规则不同；年假还取决于入职年限。"
    "例如可以问：“入职满三年的年假是多少天？”或“婚假是多少个工作日？”"
)


def focus_tokens(question: str) -> list[str]:
    """Return non-generic tokens that identify the question's subject."""

    return [
        token
        for token in lexical_tokens(question)
        if token not in _FOCUS_STOPWORDS
        and len(token) > 1
        and not any(char in _FOCUS_NOISE_CHARS for char in token)
    ]


_ASPECT_SPLIT_RE = re.compile(r"[：:，,、；;]\s*")


def article_terms(question: str) -> list[str]:
    """Extract explicit article references such as “第十九条”."""

    return list(dict.fromkeys(_ARTICLE_RE.findall(question)))


def title_focus_terms(question: str) -> list[str]:
    """Distinctive subject terms from a quoted policy/document title.

    “国务院办公厅……通知” contains many generic government words. Without this
    guard, any chunk mentioning “通知/工作/治理” can look relevant to an
    unrelated policy title.
    """

    for match in _TITLE_RE.finditer(question):
        terms = [
            token
            for token in focus_tokens(match.group(1))
            if token not in _POLICY_GENERIC_TERMS
        ]
        if terms:
            return terms
    return []


def question_aspects(question: str) -> list[frozenset[str]]:
    """Split a multi-part question into its separately answerable facets."""

    aspects: list[frozenset[str]] = []
    for segment in _ASPECT_SPLIT_RE.split(question):
        tokens = focus_tokens(segment)
        if tokens:
            aspect = frozenset(tokens)
            if aspect not in aspects:
                aspects.append(aspect)
    return aspects


def is_ambiguous_leave_question(question: str) -> bool:
    """True for questions like “员工的假期是多少天” with no leave type."""

    if any(leave_type in question for leave_type in LEAVE_TYPES):
        return False
    if not any(term in question for term in _GENERIC_LEAVE_TERMS):
        return False
    return any(term in question for term in _QUANTITY_TERMS)


def focus_coverage(question: str, texts: Iterable[str]) -> tuple[list[str], float]:
    """Fraction of focus tokens present anywhere in retrieved texts."""

    focus = focus_tokens(question)
    if not focus:
        return [], 0.0
    context_tokens: set[str] = set()
    for text in texts:
        context_tokens.update(lexical_tokens(text))
    hits = [token for token in focus if token in context_tokens]
    return focus, len(hits) / len(focus)


def answer_covers_focus(question: str, answer: str, *, context: str = "") -> bool:
    """Whether the answer covers every separately asked facet.

    A single focus hit is not enough for “多少人、多少年、风险、争议” style
    questions; each facet needs at least one supporting term. ``context`` may
    contain citation titles/sections, because a valid answer often omits the
    full official title that the question quoted.
    """

    compact_answer = re.sub(r"\s+", "", f"{answer}\n{context}")
    title_terms = title_focus_terms(question)
    if title_terms and not all(term in compact_answer for term in title_terms):
        return False
    articles = article_terms(question)
    if articles and not all(article in compact_answer for article in articles):
        return False
    aspects = question_aspects(question)
    if not aspects:
        return True
    answer_tokens = set(lexical_tokens(f"{answer}\n{context}"))
    return all(bool(aspect.intersection(answer_tokens)) for aspect in aspects)
