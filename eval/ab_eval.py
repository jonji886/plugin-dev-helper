"""Offline Prompt A/B evaluation on the same golden dataset."""

from __future__ import annotations

import json
import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

from agent import AgentRunner
from eval.gate import compare_runs
from eval.scoring import score_answer
from vector_store import VectorStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class EvaluationTimeout(BaseException):
    """Interrupt a provider call that ignores the SDK read timeout."""


@contextmanager
def _hard_timeout(seconds: float):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def raise_timeout(_signum, _frame):
        raise EvaluationTimeout(f"evaluation case exceeded {seconds:g}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _citation_validity(result: dict, index_by_id: dict, case: dict) -> bool:
    citations = result.get("citations", [])
    if case.get("expected_behavior") == "abstain":
        # 拒答也可以引用“知识库只覆盖到这里”的证据；引用为空同样合法。
        # 这里校验引用本身是否可追溯，而不是强制拒答不能带引用。
        return not citations or all(
            (entry := index_by_id.get(citation.get("id")))
            and citation.get("source", "") == entry.get("source", "")
            and citation.get("sdk_version", "") == entry.get("sdkVersion", "")
            for citation in citations
        )
    return bool(citations) and all(
        (entry := index_by_id.get(citation.get("id")))
        and citation.get("source", "") == entry.get("source", "")
        and citation.get("sdk_version", "") == entry.get("sdkVersion", "")
        for citation in citations
    )


def evaluate_variant(cases: list[dict], prompt_version: str, knowledge_index_path: Path | None = None) -> dict:
    index_path = knowledge_index_path or Path("data/knowledge/_index.json")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        index = []
    index_by_id = {entry.get("id"): entry for entry in index}
    # Prompt A/B isolates answer Prompt changes from Router network variance.
    # The final Main/Reason/Vision generation calls still use the current .env.
    runner = AgentRunner(prompt_version=prompt_version, deterministic_router=True)
    vector_store = VectorStore()
    recall_hits = correct = valid = 0
    latency = 0.0
    input_tokens = output_tokens = 0
    cost = 0.0
    failed_cases = 0
    case_timeout_seconds = float(os.getenv("EVAL_CASE_TIMEOUT_SECONDS", "90"))
    details = []
    for case in cases:
        retrieved = vector_store.search_hybrid(case["question"], top_k=5)
        retrieved_ids = [item.get("id") or item.get("metadata", {}).get("id", "") for item in retrieved]
        if any(any(ref in item for ref in case.get("reference_docs", [])) for item in retrieved_ids):
            recall_hits += 1
        started = time.perf_counter()
        try:
            with _hard_timeout(case_timeout_seconds):
                result = runner.chat(case["question"])
            evaluation_error = ""
        except EvaluationTimeout as error:
            failed_cases += 1
            evaluation_error = type(error).__name__
            result = {
                "answer": "",
                "citations": [],
                "provider": "",
                "model": "",
                "model_role": "",
                "route_reason": "evaluation hard timeout",
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost": 0.0,
            }
        latency += time.perf_counter() - started
        ok, _ = score_answer(result.get("answer", ""), case)
        citation_valid = _citation_validity(result, index_by_id, case)
        correct += int(ok)
        valid += int(citation_valid)
        input_tokens += result.get("input_tokens", 0)
        output_tokens += result.get("output_tokens", 0)
        cost += result.get("estimated_cost", 0.0)
        details.append({
            "id": case.get("id", ""), "question": case["question"],
            "expected_answer": case.get("expected_answer", ""),
            "correct": ok, "citation_valid": citation_valid,
            "answer": result.get("answer", "")[:2000],
            "citations": result.get("citations", []),
            "provider": result.get("provider", ""),
            "model": result.get("model", ""),
            "model_role": result.get("model_role", ""),
            "route_reason": result.get("route_reason", ""),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
            "estimated_cost": result.get("estimated_cost", 0.0),
            "status": "error" if evaluation_error else "success",
            "error_type": evaluation_error,
        })
        print(
            f"[eval] prompt={prompt_version} case={case.get('id', '')} "
            f"status={'error' if evaluation_error else 'success'} "
            f"latency_s={time.perf_counter() - started:.2f}",
            flush=True,
        )
    total = max(1, len(cases))
    return {"prompt_version": prompt_version, "case_count": len(cases), "Recall@5": round(recall_hits / total, 4),
            "Answer_Correctness": round(correct / total, 4), "Citation_Validity": round(valid / total, 4),
            "Avg_Latency": round(latency / total, 4), "Avg_Tokens": round((input_tokens + output_tokens) / total, 2),
            "Avg_Cost": round(cost / total, 8), "Input_Tokens": input_tokens, "Output_Tokens": output_tokens,
            "failed_cases": failed_cases, "failure_rate": round(failed_cases / total, 4),
            "details": details}


def evaluate_ab(cases: list[dict], baseline_version: str, candidate_version: str,
                thresholds: dict | None = None, run_metadata: dict | None = None) -> dict:
    baseline = evaluate_variant(cases, baseline_version)
    candidate = evaluate_variant(cases, candidate_version)
    return {
        "run": run_metadata or {},
        "baseline": baseline,
        "candidate": candidate,
        "gate": compare_runs(baseline, candidate, thresholds),
    }
