"""Run the same golden dataset against two Git-based Prompt versions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.ab_eval import evaluate_ab
from app.model_router import ModelRouter
from app.prompt_registry import PromptRegistry


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_metadata(args, cases: list[dict]) -> dict:
    router = ModelRouter()
    routes = router.role_routes() if router.uses_role_config() else router.profile_routes()
    prompt_registry = PromptRegistry()
    dataset_path = Path(args.dataset)
    pricing_path = Path("config/model_pricing.json")
    try:
        pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pricing = {}
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_mode": "real_llm" if router.is_ready() else "local_fallback",
        "router_mode": "deterministic_for_eval",
        "case_timeout_seconds": float(os.getenv("EVAL_CASE_TIMEOUT_SECONDS", "90")),
        "answer_context_max_chars": int(os.getenv("ANSWER_CONTEXT_MAX_CHARS", "6000")),
        "dataset_path": dataset_path.as_posix(),
        "dataset_sha256": _sha256(dataset_path),
        "dataset_case_count": len(cases),
        "baseline_prompt_version": args.baseline,
        "candidate_prompt_version": args.candidate,
        "prompt_metadata": {
            name: {
                version: prompt_registry.metadata(name, version)
                for version in (args.baseline, args.candidate)
                if (prompt_registry.root / name / f"{version}.md").exists()
            }
            for name in ("developer_qa", "query_rewrite", "intent_classifier")
        },
        "model_routes": {
            profile: {
                "provider": route.provider,
                "model": route.model,
                "role": route.role,
                "reason": route.reason,
            }
            for profile, route in routes.items()
        },
        "cost_currencies": sorted({
            pricing.get(f"{route.provider}:{route.model}", {}).get("currency", "unknown")
            for route in routes.values()
            if isinstance(pricing.get(f"{route.provider}:{route.model}"), dict)
        }),
        "pricing_file": pricing_path.as_posix(),
        "pricing_retrieved_at": pricing.get("_metadata", {}).get("siliconflow_retrieved_at", ""),
        "python": platform.python_version(),
        "langfuse_enabled": os.getenv("LANGFUSE_ENABLED", "false").lower() in {"1", "true", "yes"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Prompt A/B evaluation")
    parser.add_argument("--baseline", default="v1")
    parser.add_argument("--candidate", default="v2")
    parser.add_argument("--dataset", default="eval/test_data.json")
    parser.add_argument("--gate", default="eval/gate.json")
    parser.add_argument("--output", default="eval/prompt_eval_results.json")
    args = parser.parse_args()
    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    thresholds = json.loads(Path(args.gate).read_text(encoding="utf-8"))
    report = evaluate_ab(
        cases, args.baseline, args.candidate, thresholds,
        run_metadata=_run_metadata(args, cases),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "run": report["run"],
        "baseline": {key: value for key, value in report["baseline"].items() if key != "details"},
        "candidate": {key: value for key, value in report["candidate"].items() if key != "details"},
        "gate": report["gate"],
    }, ensure_ascii=False, indent=2))
    print(f"\nRegression Gate: {report['gate']['status']}")
    for reason in report["gate"]["reasons"]:
        print(f"- {reason}")
    return 0 if report["gate"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
