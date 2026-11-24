"""Question intent helpers: focus terms and ambiguity guards."""

from __future__ import annotations

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
_QUANTITY_TERMS = ("多少天", "几天", "多少", "天数", "多长时间")

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
        if token not in _FOCUS_STOPWORDS and len(token) > 1
    ]


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


def answer_covers_focus(question: str, answer: str) -> bool:
    """Whether the answer mentions at least one focus term from the question."""

    focus = focus_tokens(question)
    if not focus:
        return True
    answer_tokens = set(lexical_tokens(answer))
    return bool(set(focus).intersection(answer_tokens))
