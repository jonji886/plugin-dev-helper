"""评测用的可解释答案评分工具。"""

from __future__ import annotations

import re


ABSTENTION_MARKERS = (
    "知识库中没有找到",
    "无法确认",
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


def score_answer(answer: str, case: dict) -> tuple[bool, float]:
    """返回（是否通过、关键词命中率）。"""
    if case.get("expected_behavior") == "abstain":
        matched = any(marker in answer for marker in ABSTENTION_MARKERS)
        return matched, 1.0 if matched else 0.0

    keywords = expected_keywords(case)
    if not keywords:
        return False, 0.0

    hits = sum(1 for keyword in keywords if keyword in answer)
    ratio = hits / len(keywords)
    threshold = float(case.get("min_keyword_ratio", 0.5))
    return ratio >= threshold, ratio
