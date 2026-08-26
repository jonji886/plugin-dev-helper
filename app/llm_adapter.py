"""Provider-neutral LLM adapters and provider failover.

The application routes by role, while this module hides OpenAI-compatible
provider details, usage instrumentation, and transient-error failover behind
one adapter contract.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from langchain_openai import ChatOpenAI

from app.llm_usage import (
    calculate_cost,
    current_request_usage,
    extract_usage,
    record_request_usage,
)
from app.observability import Observability
from app.model_router import ModelRoute
from app.prompt_registry import PromptRegistry


class LLMAdapter(Protocol):
    """Stable application-facing contract for one downstream LLM route."""

    route: ModelRoute

    @property
    def stats(self) -> dict[str, int | float]:
        ...

    def invoke(
        self,
        messages: list[Any],
        *,
        prompt_name: str = "developer_qa",
        trace: object | None = None,
        task_type: str = "general",
    ) -> Any:
        ...

    def supports_images(self) -> bool:
        ...


def provider_runtime_config(provider: str) -> tuple[str, str | None]:
    """Resolve credentials and endpoint without exposing secrets."""
    normalized = provider.lower().replace("-", "").replace("_", "")
    if normalized == "siliconflow":
        return (
            os.environ.get("SILICONFLOW_API_KEY", "").strip(),
            os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").strip(),
        )
    if normalized == "deepseek":
        return (
            os.environ.get("DEEPSEEK_API_KEY", "").strip(),
            # Official DeepSeek OpenAI-compatible endpoint.
            os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        )
    if normalized == "openai":
        return (
            os.environ.get("OPENAI_API_KEY", "").strip(),
            os.environ.get("OPENAI_BASE_URL", "").strip() or None,
        )

    prefix = provider.upper().replace("-", "_")
    return (
        os.environ.get(f"{prefix}_API_KEY", "").strip(),
        os.environ.get(f"{prefix}_BASE_URL", "").strip() or None,
    )


class OpenAICompatibleAdapter:
    """Adapter around one LangChain OpenAI-compatible client."""

    def __init__(self, client: Any, route: ModelRoute, image_support: bool = False):
        self.client = client
        self.route = route
        self._image_support = image_support

    @property
    def stats(self) -> dict[str, int | float]:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": 0.0}

    def invoke(self, messages: list[Any], **_: Any) -> Any:
        return self.client.invoke(messages)

    def supports_images(self) -> bool:
        return self._image_support


class InstrumentedAdapter:
    """Add prompt metadata, usage, cost, and tracing around a downstream adapter."""

    def __init__(
        self,
        adapter: LLMAdapter,
        prompt_registry: PromptRegistry,
        observability: Observability,
        trace: object | None = None,
        prompt_version: str = "v1",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        max_retries: int = 2,
    ):
        self.adapter = adapter
        self.route = adapter.route
        self.prompt_registry = prompt_registry
        self.observability = observability
        self.trace = trace
        self.prompt_version = prompt_version
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                      "estimated_cost": 0.0, "prompt_versions": {}}

    def invoke(
        self,
        messages: list[Any],
        *,
        prompt_name: str = "developer_qa",
        trace: object | None = None,
        task_type: str = "general",
    ) -> Any:
        prompt_version = self.prompt_version if prompt_name == "developer_qa" else "v1"
        prompt_metadata = self.prompt_registry.metadata(prompt_name, prompt_version)
        prompt_text = "\n".join(str(getattr(message, "content", "")) for message in messages)
        with self.observability.span(trace or self.trace or object(), f"llm:{prompt_name}", {
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "prompt_status": prompt_metadata.get("status", ""),
            "provider": self.route.provider,
            "model": self.route.model,
            "route_reason": self.route.reason,
            "model_role": self.route.role,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }) as span:
            response = self.adapter.invoke(messages, prompt_name=prompt_name,
                                            trace=trace, task_type=task_type)
            output_text = str(getattr(response, "content", ""))
            usage = extract_usage(response, prompt_text, output_text)
            self.stats["input_tokens"] += usage["input_tokens"]
            self.stats["output_tokens"] += usage["output_tokens"]
            self.stats["total_tokens"] += usage["total_tokens"]
            request_cost = calculate_cost(
                self.route.provider,
                self.route.model,
                usage["input_tokens"],
                usage["output_tokens"],
            )
            self.stats["estimated_cost"] += request_cost
            record_request_usage(usage, request_cost)
            self.stats["prompt_versions"][prompt_name] = prompt_version
            self.observability.update(
                span,
                usage=usage,
                model=self.route.model,
                provider=self.route.provider,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                prompt_status=prompt_metadata.get("status", ""),
                route_reason=self.route.reason,
                model_role=self.route.role,
                temperature=self.temperature,
                estimated_cost=request_cost,
            )
            return response

    def supports_images(self) -> bool:
        return self.adapter.supports_images()


def _message_has_image(messages: list[Any]) -> bool:
    for message in messages:
        content = getattr(message, "content", "")
        if not isinstance(content, list):
            continue
        if any(isinstance(part, dict) and part.get("type") in {"image_url", "image"}
               for part in content):
            return True
    return False


def is_retryable_provider_error(error: Exception) -> bool:
    """Only fail over transient provider failures, never bad credentials/payloads."""
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    if status is not None:
        try:
            return int(status) in {408, 425, 429, 500, 502, 503, 504}
        except (TypeError, ValueError):
            pass

    message = f"{type(error).__name__}: {error}".lower()
    return any(marker in message for marker in (
        "timeout", "timed out", "connection", "server disconnected",
        "temporarily unavailable", "rate limit", "overloaded",
    ))


class FailoverAdapter:
    """Try a backup adapter only for transient primary-provider failures."""

    def __init__(
        self,
        primary: LLMAdapter,
        fallback: LLMAdapter | None = None,
        observability: Observability | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.observability = observability
        self.last_route = primary.route

    @property
    def route(self) -> ModelRoute:
        return self.primary.route

    @property
    def stats(self) -> dict[str, int | float]:
        request_usage = current_request_usage()
        if request_usage is not None:
            return request_usage
        stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": 0.0}
        for adapter in (self.primary, self.fallback):
            if adapter is None:
                continue
            for key in stats:
                stats[key] += adapter.stats.get(key, 0)
        return stats

    def supports_images(self) -> bool:
        return self.primary.supports_images() or bool(self.fallback and self.fallback.supports_images())

    def invoke(
        self,
        messages: list[Any],
        *,
        prompt_name: str = "developer_qa",
        trace: object | None = None,
        task_type: str = "general",
    ) -> Any:
        self.last_route = self.primary.route
        try:
            return self.primary.invoke(messages, prompt_name=prompt_name,
                                       trace=trace, task_type=task_type)
        except Exception as error:
            if self.fallback is None or not is_retryable_provider_error(error):
                raise
            if _message_has_image(messages) and not self.fallback.supports_images():
                raise

            if self.observability:
                self.observability.update(
                    trace or object(),
                    fallback_used=True,
                    primary_provider=self.primary.route.provider,
                    primary_model=self.primary.route.model,
                    fallback_provider=self.fallback.route.provider,
                    fallback_model=self.fallback.route.model,
                    fallback_error_type=type(error).__name__,
                )
            response = self.fallback.invoke(messages, prompt_name=prompt_name,
                                            trace=trace, task_type=task_type)
            self.last_route = ModelRoute(
                provider=self.fallback.route.provider,
                model=self.fallback.route.model,
                reason=(f"{self.fallback.route.reason}; primary "
                        f"{self.primary.route.provider} failed with {type(error).__name__}"),
                profile=self.primary.route.profile,
                role=self.primary.route.role,
            )
            return response


def create_instrumented_adapter(
    *,
    route: ModelRoute,
    timeout_seconds: float,
    max_retries: int,
    max_tokens: int,
    prompt_registry: PromptRegistry,
    observability: Observability,
    trace: object | None = None,
    prompt_version: str = "v1",
    image_support: bool | None = None,
) -> InstrumentedAdapter | None:
    """Create one provider adapter, returning None when its key is absent."""
    api_key, base_url = provider_runtime_config(route.provider)
    if not api_key:
        print(f"[env] {route.provider} API key 未加载")
        return None
    print(f"[env] {route.provider} API key 已加载，model={route.model}")
    if image_support is None:
        image_support = route.role == "vision" and route.provider.lower() != "deepseek"
    client = ChatOpenAI(
        model=route.model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,
        max_tokens=max_tokens,
        timeout=timeout_seconds,
        max_retries=max_retries,
    )
    downstream = OpenAICompatibleAdapter(client, route, image_support=image_support)
    return InstrumentedAdapter(
        downstream,
        prompt_registry,
        observability,
        trace=trace,
        prompt_version=prompt_version,
        temperature=0.1,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )
