"""Usage extraction and cost calculation for OpenAI-compatible responses."""

from __future__ import annotations

from contextvars import ContextVar, Token
import json
from pathlib import Path


DEFAULT_PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "model_pricing.json"

_REQUEST_USAGE: ContextVar[dict[str, int | float] | None] = ContextVar(
    "llm_request_usage", default=None
)


def start_request_usage() -> Token:
    """Start an isolated usage accumulator for one request."""
    return _REQUEST_USAGE.set({
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
    })


def current_request_usage() -> dict[str, int | float] | None:
    """Return a copy of the current request usage, if tracking is active."""
    usage = _REQUEST_USAGE.get()
    return dict(usage) if usage is not None else None


def record_request_usage(usage: dict[str, int], estimated_cost: float) -> None:
    """Add one provider call to the active request-local usage."""
    current = _REQUEST_USAGE.get()
    if current is None:
        return
    current["input_tokens"] += usage.get("input_tokens", 0)
    current["output_tokens"] += usage.get("output_tokens", 0)
    current["total_tokens"] += usage.get("total_tokens", 0)
    current["estimated_cost"] += estimated_cost


def reset_request_usage(token: Token) -> None:
    """Restore the parent context after a request finishes."""
    _REQUEST_USAGE.reset(token)


def _number(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def extract_usage(response: object, input_text: str = "", output_text: str = "") -> dict[str, int]:
    """Read provider usage metadata, falling back to a transparent estimate."""
    usage: object = getattr(response, "usage_metadata", None)
    if not usage:
        metadata = getattr(response, "response_metadata", {}) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _number(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _number(usage.get("output_tokens", usage.get("completion_tokens")))
    if not input_tokens and input_text:
        input_tokens = max(1, len(input_text) // 4)
    if not output_tokens and output_text:
        output_tokens = max(1, len(output_text) // 4)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens}


def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int,
                   pricing_path: Path | str = DEFAULT_PRICING_PATH) -> float:
    try:
        pricing = json.loads(Path(pricing_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pricing = {}
    item = pricing.get(f"{provider}:{model}") or pricing.get(model) or {}
    if not isinstance(item, dict):
        return 0.0

    # SiliconFlow and other providers may publish input-length price tiers.
    # Select the first tier whose upper bound contains the request; the last
    # tier is the open-ended fallback.
    tier = item
    for candidate in item.get("tiers", []):
        max_input_tokens = candidate.get("max_input_tokens")
        if max_input_tokens is None or input_tokens <= max_input_tokens:
            tier = {**item, **candidate}
            break

    return round((input_tokens / tier.get("input_unit", item.get("input_unit", 1_000_000)))
                 * tier.get("input_price", 0)
                 + (output_tokens / tier.get("output_unit", item.get("output_unit", 1_000_000)))
                 * tier.get("output_price", 0), 8)
