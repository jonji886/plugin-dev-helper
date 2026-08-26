"""评测用的可解释答案评分工具。"""

from __future__ import annotations

import re


ABSTENTION_MARKERS = (
    "知识库中没有找到",
    "无法确认",
    "无法回答",
    "无法准确回答",
    "无法给出确切",
    "信息不足",
    "请查阅",
    "没有相关信息",
)


def expected_keywords(case: dict) -> list[str]:
    """读取显式关键词，或从参考答案中拆分关键词。"""
    explicit = case.get("expected_keywords")
    if isinstance(explicit, list):
        return [str(keyword).strip() for keyword in explicit if str(keyword).strip()]

    expected = str(case.get("expected_answer", ""))
    return [part.strip() for part in re.split(r"[，,。；;、\n]+", expected) if part.strip()]


def _normalize(text: str) -> str:
    """Normalize Markdown/code punctuation without changing Chinese words."""
    return re.sub(r"\s+", "", str(text).lower().replace("`", "").replace("(", "").replace(")", ""))


def _keyword_fragments(keyword: str) -> list[str]:
    normalized = _normalize(keyword)
    # Keep API/symbol names as atomic fragments and use contiguous Chinese
    # phrases for natural-language parts of an expected answer.
    return re.findall(r"[a-z_][a-z0-9_.]*(?:\(\))?|[\u4e00-\u9fff]+|\d+(?:\.\d+)*", normalized)


def _keyword_matches(answer: str, keyword: str) -> bool:
    normalized_answer = _normalize(answer)
    normalized_keyword = _normalize(keyword)
    if normalized_keyword and normalized_keyword in normalized_answer:
        return True
    fragments = [fragment for fragment in _keyword_fragments(keyword) if len(fragment) > 1 or re.search(r"[a-z0-9]", fragment)]
    if not fragments:
        return False
    matched = sum(fragment in normalized_answer for fragment in fragments)
    # A generated answer may say “作用是” where the reference says “调用”;
    # require most meaningful fragments rather than an exact sentence match.
    return matched / len(fragments) >= 0.5


def score_answer(answer: str, case: dict) -> tuple[bool, float]:
    """返回（是否通过、关键词命中率）。"""
    if case.get("expected_behavior") == "abstain":
        matched = any(marker in answer for marker in ABSTENTION_MARKERS)
        return matched, 1.0 if matched else 0.0

    keywords = expected_keywords(case)
    if not keywords:
        return False, 0.0

    hits = sum(1 for keyword in keywords if _keyword_matches(answer, keyword))
    ratio = hits / len(keywords)
    threshold = float(case.get("min_keyword_ratio", 0.5))
    return ratio >= threshold, ratio
