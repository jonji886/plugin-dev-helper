import os
import unittest
from unittest.mock import patch

from app.llm_adapter import FailoverAdapter, is_retryable_provider_error, provider_runtime_config
from app.model_router import ModelRoute
from agent.assistant import _deepseek_fallback_route


class FakeAdapter:
    def __init__(self, route, result=None, error=None, image_support=False):
        self.route = route
        self.result = result
        self.error = error
        self._image_support = image_support
        self.stats = {"input_tokens": 0, "output_tokens": 0,
                      "total_tokens": 0, "estimated_cost": 0.0}
        self.calls = 0

    def invoke(self, messages, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    def supports_images(self):
        return self._image_support


class LLMAdapterTests(unittest.TestCase):
    def test_only_transient_errors_are_retryable(self):
        self.assertTrue(is_retryable_provider_error(TimeoutError("read timeout")))
        self.assertTrue(is_retryable_provider_error(RuntimeError("server disconnected")))

        auth_error = RuntimeError("401 authentication failed")
        auth_error.status_code = 401
        self.assertFalse(is_retryable_provider_error(auth_error))

        rate_error = RuntimeError("rate limit")
        rate_error.status_code = 429
        self.assertTrue(is_retryable_provider_error(rate_error))

    def test_failover_adapter_switches_to_deepseek_after_transient_failure(self):
        primary = FakeAdapter(
            ModelRoute("siliconflow", "deepseek-ai/DeepSeek-V4-Flash", "primary", "main", "main"),
            error=TimeoutError("SiliconFlow timeout"),
        )
        fallback = FakeAdapter(
            ModelRoute("deepseek", "deepseek-v4-flash", "backup", "main", "main"),
            result="fallback answer",
        )
        adapter = FailoverAdapter(primary, fallback)

        self.assertEqual(adapter.invoke([]), "fallback answer")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(adapter.last_route.provider, "deepseek")
        self.assertIn("TimeoutError", adapter.last_route.reason)

    def test_failover_does_not_send_images_to_text_only_backup(self):
        primary = FakeAdapter(
            ModelRoute("siliconflow", "vision-model", "primary", "vision", "vision"),
            error=TimeoutError("timeout"), image_support=True,
        )
        fallback = FakeAdapter(
            ModelRoute("deepseek", "deepseek-v4-flash", "backup", "vision", "vision"),
            result="should not run", image_support=False,
        )
        adapter = FailoverAdapter(primary, fallback)
        image_message = type("Message", (), {
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}],
        })()

        with self.assertRaises(TimeoutError):
            adapter.invoke([image_message])
        self.assertEqual(fallback.calls, 0)

    def test_deepseek_route_uses_official_v4_model_names(self):
        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_FALLBACK_ENABLED": "true",
        }, clear=False):
            main = _deepseek_fallback_route(
                ModelRoute("siliconflow", "main-model", "primary", "main", "main")
            )
            reason = _deepseek_fallback_route(
                ModelRoute("siliconflow", "reason-model", "primary", "reason", "reason")
            )
            vision = _deepseek_fallback_route(
                ModelRoute("siliconflow", "vision-model", "primary", "vision", "vision")
            )

        self.assertEqual(main.model, "deepseek-v4-flash")
        self.assertEqual(reason.model, "deepseek-v4-flash")
        self.assertIsNone(vision)

    def test_official_deepseek_endpoint_is_the_default(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            self.assertEqual(
                provider_runtime_config("deepseek"),
                ("test-key", "https://api.deepseek.com"),
            )


if __name__ == "__main__":
    unittest.main()
