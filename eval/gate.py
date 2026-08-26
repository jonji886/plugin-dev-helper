"""Regression gate for comparing two offline evaluation runs."""

from __future__ import annotations

DEFAULT_THRESHOLDS = {"recall_at_5_drop": 0.03, "correctness_drop": 0.03, "citation_validity_min": 0.90}


def compare_runs(baseline: dict, candidate: dict, thresholds: dict | None = None) -> dict:
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    deltas = {name: candidate.get(name, 0) - baseline.get(name, 0)
              for name in ("Recall@5", "Answer_Correctness", "Citation_Validity")}
    reasons = []
    if deltas["Recall@5"] < -limits["recall_at_5_drop"]:
        reasons.append(f"Recall@5 dropped by {-deltas['Recall@5']:.2%}")
    if deltas["Answer_Correctness"] < -limits["correctness_drop"]:
        reasons.append(f"Answer correctness dropped by {-deltas['Answer_Correctness']:.2%}")
    if candidate.get("Citation_Validity", 0) < limits["citation_validity_min"]:
        reasons.append(f"Citation validity is below {limits['citation_validity_min']:.2%}")
    return {"status": "PASS" if not reasons else "FAIL", "deltas": deltas, "reasons": reasons, "thresholds": limits}
