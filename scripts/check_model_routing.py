"""Run a small live routing smoke test and summarize runtime metrics."""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _solid_png_data_url(width: int = 32, height: int = 32) -> str:
    raw = b"".join(b"\x00" + bytes([0, 180, 80, 255]) * width for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _request(base_url: str, path: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def _metrics_delta(before: dict, after: dict) -> dict:
    """Extract batch-level counters from the two aggregate snapshots."""
    fields = ("total_requests", "successful_requests", "failed_requests", "total_tokens", "estimated_cost")
    return {
        f: round(float(after.get(f, 0)) - float(before.get(f, 0)), 8)
        for f in fields
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Router/Main/Reason/Vision smoke test")
    parser.add_argument("--base-url", default=os.getenv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--dataset", default="eval/model_routing_cases.json")
    parser.add_argument("--output", default="eval/model_routing_results.json")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    cases = json.loads((PROJECT_ROOT / args.dataset).read_text(encoding="utf-8"))
    before = _request(args.base_url, "/api/metrics")
    results = []
    for case in cases:
        images = list(case.get("images", []))
        if case.get("image_fixture") == "solid_32x32_png":
            images = [_solid_png_data_url()]
        started = time.perf_counter()
        try:
            response = _request(
                args.base_url,
                "/api/chat",
                {"query": case["query"], "images": images},
                timeout=args.timeout,
            )
            result = {
                "id": case["id"],
                "category": case["category"],
                "expected_role": case["expected_role"],
                "actual_role": response.get("model_role", ""),
                "role_match": response.get("model_role") == case["expected_role"],
                "request_id": response.get("request_id", ""),
                "provider": response.get("provider", ""),
                "model": response.get("model", ""),
                "image_count": response.get("image_count", len(images)),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "status": "success",
            }
        except Exception as error:
            result = {
                "id": case["id"],
                "category": case["category"],
                "expected_role": case["expected_role"],
                "actual_role": "",
                "role_match": False,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "status": "error",
                "error": str(error),
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))

    after = _request(args.base_url, "/api/metrics")
    failures = sum(result["status"] != "success" for result in results)
    mismatches = sum(not result["role_match"] for result in results)
    report = {
        "dataset": args.dataset,
        "base_url": args.base_url,
        "cases": results,
        "summary": {
            "total_cases": len(results),
            "successful_cases": len(results) - failures,
            "failed_cases": failures,
            "batch_failure_rate": round(failures / len(results), 4) if results else 0.0,
            "role_matches": len(results) - mismatches,
            "role_match_rate": round((len(results) - mismatches) / len(results), 4) if results else 0.0,
        },
        "metrics_delta": _metrics_delta(before, after),
        "metrics_before": before,
        "metrics_after": after,
    }
    output_path = PROJECT_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(after, ensure_ascii=False, indent=2))
    return 1 if failures or mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
