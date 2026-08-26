"""Request-level failover acceptance tests.

These tests exercise the same FastAPI ``/api/chat`` and ``/api/metrics``
path used by the running service while injecting a deterministic provider
timeout.  They intentionally do not spend tokens or depend on a live LLM.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

import app.main as main
from app.llm_adapter import FailoverAdapter, InstrumentedAdapter
from app.llm_usage import current_request_usage, reset_request_usage, start_request_usage
from app.metrics_store import MetricsStore
from app.model_router import ModelRoute
from app.observability import Observability
from app.prompt_registry import PromptRegistry


class FakeResponse:
    def __init__(self, content: str, input_tokens: int, output_tokens: int):
        self.content = content
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }


class FakeProvider:
    def __init__(self, route: ModelRoute, response=None, error: Exception | None = None,
                 image_support: bool = False):
        self.route = route
        self.response = response
        self.error = error
        self.image_support = image_support
        self.calls = 0

    def invoke(self, _messages, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response

    def supports_images(self) -> bool:
        return self.image_support


class FailoverRequestRunner:
    """Small AgentRunner substitute that preserves the production response shape."""

    def __init__(self, primary_error: Exception | None = None):
        observability = Observability(enabled=False)
        registry = PromptRegistry()
        primary_route = ModelRoute(
            "siliconflow", "deepseek-ai/DeepSeek-V4-Flash", "configured main role", "main", "main"
        )
        fallback_route = ModelRoute(
            "deepseek", "deepseek-v4-flash", "official DeepSeek fallback", "main", "main"
        )
        self.primary_provider = FakeProvider(
            primary_route, error=primary_error, image_support=False
        )
        self.fallback_provider = FakeProvider(
            fallback_route,
            response=FakeResponse("DeepSeek fallback answer", input_tokens=11, output_tokens=4),
        )
        primary = InstrumentedAdapter(
            self.primary_provider, registry, observability, prompt_version="v1", max_tokens=4096
        )
        fallback = InstrumentedAdapter(
            self.fallback_provider, registry, observability, prompt_version="v1", max_tokens=4096
        )
        self.llm = FailoverAdapter(primary, fallback, observability)

    def chat(self, query: str, session_id=None, request_id=None, images=None):
        usage_token = start_request_usage()
        try:
            response = self.llm.invoke([type("Message", (), {"content": query})()])
            usage = current_request_usage() or {
                "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "estimated_cost": 0.0,
            }
            route = self.llm.last_route
            return {
                "answer": response.content,
                "session_id": session_id or "a1b2c3d4",
                "intent": "general",
                "retrieved_docs": [{"id": "IDP.Miniapp.exit"}],
                "citations": [{
                    "id": "IDP.Miniapp.exit", "source": "index.d.ts",
                    "sdk_version": "1.83.0", "start_line": 10, "end_line": 12,
                }],
                "provider": route.provider,
                "model": route.model,
                "model_role": route.role,
                "route_reason": route.reason,
                "image_count": len(images or []),
                "prompt_name": "developer_qa",
                "prompt_version": "v1",
                **usage,
            }
        finally:
            reset_request_usage(usage_token)


class FailoverRequestTests(unittest.TestCase):
    def test_chat_switches_provider_and_records_request_metrics(self):
        previous_runner = main.agent_runner
        previous_metrics_store = main.metrics_store
        runner = FailoverRequestRunner(TimeoutError("SiliconFlow read timeout"))
        main.agent_runner = runner

        with tempfile.TemporaryDirectory() as directory:
            main.metrics_store = MetricsStore(Path(directory) / "app.sqlite3")

            async def request():
                transport = httpx.ASGITransport(app=main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/api/chat", json={"query": "如何退出？"})
                    metrics = await client.get("/api/metrics")
                    return response, metrics

            try:
                response, metrics_response = asyncio.run(request())
                body = response.json()
                metrics = metrics_response.json()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(body["provider"], "deepseek")
                self.assertEqual(body["model"], "deepseek-v4-flash")
                self.assertEqual(body["model_role"], "main")
                self.assertIn("TimeoutError", body["route_reason"])
                self.assertEqual(runner.primary_provider.calls, 1)
                self.assertEqual(runner.fallback_provider.calls, 1)

                self.assertEqual(metrics["total_requests"], 1)
                self.assertEqual(metrics["successful_requests"], 1)
                self.assertEqual(metrics["failed_requests"], 0)
                self.assertEqual(metrics["total_tokens"], 15)
                self.assertGreater(metrics["estimated_cost"], 0)
                self.assertEqual(metrics["by_model_role"]["main"]["total_tokens"], 15)
                self.assertGreater(metrics["by_model_role"]["main"]["estimated_cost"], 0)
            finally:
                main.agent_runner = previous_runner
                main.metrics_store = previous_metrics_store

    def test_request_test_injects_timeout_without_calling_a_live_provider(self):
        runner = FailoverRequestRunner(TimeoutError("injected timeout"))
        self.assertIsInstance(runner.llm, FailoverAdapter)
        self.assertEqual(runner.primary_provider.route.provider, "siliconflow")
        self.assertEqual(runner.fallback_provider.route.model, "deepseek-v4-flash")

    def test_auth_errors_are_recorded_as_failures_without_fallback(self):
        auth_error = RuntimeError("authentication failed")
        auth_error.status_code = 401
        previous_runner = main.agent_runner
        previous_metrics_store = main.metrics_store
        runner = FailoverRequestRunner(auth_error)
        main.agent_runner = runner

        with tempfile.TemporaryDirectory() as directory:
            main.metrics_store = MetricsStore(Path(directory) / "app.sqlite3")

            async def request():
                transport = httpx.ASGITransport(app=main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/api/chat", json={"query": "如何退出？"})
                    metrics = await client.get("/api/metrics")
                    return response, metrics

            try:
                response, metrics_response = asyncio.run(request())
                self.assertEqual(response.status_code, 500)
                self.assertEqual(runner.primary_provider.calls, 1)
                self.assertEqual(runner.fallback_provider.calls, 0)
                metrics = metrics_response.json()
                self.assertEqual(metrics["total_requests"], 1)
                self.assertEqual(metrics["successful_requests"], 0)
                self.assertEqual(metrics["failed_requests"], 1)
                self.assertEqual(metrics["failure_rate"], 1.0)
            finally:
                main.agent_runner = previous_runner
                main.metrics_store = previous_metrics_store


if __name__ == "__main__":
    unittest.main()
