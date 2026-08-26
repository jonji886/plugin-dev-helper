import json
import tempfile
import unittest
from pathlib import Path

from app.llm_usage import (
    calculate_cost,
    current_request_usage,
    extract_usage,
    record_request_usage,
    reset_request_usage,
    start_request_usage,
)


class CostTrackingTests(unittest.TestCase):
    def test_extracts_provider_usage_and_falls_back_to_estimate(self):
        response = type("Response", (), {
            "usage_metadata": {"input_tokens": 120, "output_tokens": 30},
        })()
        self.assertEqual(extract_usage(response), {
            "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
        })

        estimated = extract_usage(object(), "x" * 40, "y" * 20)
        self.assertEqual(estimated["total_tokens"], 15)

    def test_uses_current_siliconflow_prices_and_glm_tiers(self):
        self.assertEqual(calculate_cost("siliconflow", "Qwen/Qwen3-8B", 1000, 1000), 0.0)
        self.assertEqual(calculate_cost("siliconflow", "deepseek-ai/DeepSeek-V4-Flash", 1_000_000, 1_000_000), 3.0)
        self.assertEqual(calculate_cost("siliconflow", "Qwen/Qwen3-VL-32B-Instruct", 1_000_000, 1_000_000), 5.0)
        self.assertEqual(calculate_cost("deepseek", "deepseek-v4-flash", 1_000_000, 1_000_000), 0.42)
        self.assertEqual(calculate_cost("deepseek", "deepseek-v4-pro", 1_000_000, 1_000_000), 1.305)
        self.assertEqual(calculate_cost("siliconflow", "Pro/zai-org/GLM-5.1", 32_000, 1_000_000), 24.192)
        self.assertEqual(calculate_cost("siliconflow", "Pro/zai-org/GLM-5.1", 32_001, 1_000_000), 28.256008)

    def test_unknown_model_is_explicitly_zero_without_fabricating_cost(self):
        self.assertEqual(calculate_cost("siliconflow", "unknown/model", 1000, 1000), 0.0)

    def test_custom_pricing_file_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.json"
            path.write_text(json.dumps({"provider:model": {
                "currency": "USD", "input_price": 1, "output_price": 2,
                "input_unit": 1000, "output_unit": 1000,
            }}), encoding="utf-8")
            self.assertEqual(calculate_cost("provider", "model", 1000, 1000, path), 3.0)

    def test_request_usage_isolated_from_process_totals(self):
        self.assertIsNone(current_request_usage())
        token = start_request_usage()
        try:
            record_request_usage({
                "input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
            }, 0.25)
            record_request_usage({
                "input_tokens": 6, "output_tokens": 2, "total_tokens": 8,
            }, 0.05)
            self.assertEqual(current_request_usage(), {
                "input_tokens": 16, "output_tokens": 6,
                "total_tokens": 22, "estimated_cost": 0.3,
            })
        finally:
            reset_request_usage(token)
        self.assertIsNone(current_request_usage())


if __name__ == "__main__":
    unittest.main()
