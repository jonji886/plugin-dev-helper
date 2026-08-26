import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app.model_router import ModelRoute, ModelRouter, infer_task_type
from agent.assistant import AnswerGenerator, IntentRouter, _llm_runtime_limits, _provider_runtime_config
from app.metrics_store import MetricsStore
from eval.gate import compare_runs


class AIQualityLoopTests(unittest.TestCase):
    def test_router_uses_configured_profiles_and_falls_back_to_default(self):
        router = ModelRouter({
            "DEFAULT_LLM_PROVIDER": "deepseek", "DEFAULT_LLM_MODEL": "deepseek-chat",
            "FAST_LLM_PROVIDER": "deepseek", "FAST_LLM_MODEL": "deepseek-fast",
            "STRONG_LLM_PROVIDER": "deepseek", "STRONG_LLM_MODEL": "deepseek-reasoner",
        })
        self.assertEqual(router.route("api", 100).profile, "fast")
        self.assertEqual(router.route("code", 9000).model, "deepseek-reasoner")
        self.assertEqual(router.route("general", 9000).profile, "default")
        self.assertEqual(ModelRouter({"DEFAULT_LLM_MODEL": "only-model"}).route("api", 100).model, "only-model")

    def test_router_maps_siliconflow_model_convention_to_three_profiles(self):
        router = ModelRouter({
            "SILICONFLOW_API_KEY": "test-key",
            "MODEL_DEEPSEEK": "deepseek-model",
            "MODEL_QWEN": "qwen-model",
            "MODEL_GLM": "glm-model",
        })
        self.assertEqual(router.route("general").model, "glm-model")
        self.assertEqual(router.route("api", 100).model, "qwen-model")
        self.assertEqual(router.route("code", 9000).model, "deepseek-model")
        self.assertEqual(router.route("general").provider, "siliconflow")
        self.assertTrue(router.is_ready())

    def test_explicit_profile_overrides_siliconflow_convention(self):
        router = ModelRouter({
            "SILICONFLOW_API_KEY": "test-key",
            "MODEL_QWEN": "qwen-model",
            "FAST_LLM_PROVIDER": "openai",
            "FAST_LLM_MODEL": "gpt-fast",
        })
        route = router.route("api", 100)
        self.assertEqual((route.provider, route.model), ("openai", "gpt-fast"))

    def test_four_role_router_selects_router_main_reason_and_vision(self):
        router = ModelRouter({
            "SILICONFLOW_API_KEY": "test-key",
            "ROUTER": "router-model",
            "MAIN": "main-model",
            "REASON": "reason-model",
            "VISION": "vision-model",
        })
        self.assertTrue(router.uses_role_config())
        self.assertEqual(router.route("intent_classifier").role, "router")
        self.assertEqual(router.route("general").model, "main-model")
        self.assertEqual(router.route("code").model, "reason-model")
        self.assertEqual(router.route("general", has_image=True).model, "vision-model")
        self.assertEqual(router.route("general", confidence=0.5).role, "reason")
        self.assertTrue(router.is_ready())
        self.assertFalse(router.role_status()["vision"]["fallback"])

    def test_provider_unavailable_is_exposed_without_crashing_routing(self):
        router = ModelRouter({"ROUTER": "router-model", "MAIN": "main-model"})
        self.assertEqual(router.route("general").model, "main-model")
        self.assertFalse(router.is_ready())
        self.assertFalse(router.role_status()["main"]["available"])

    def test_image_question_reaches_vision_without_rag_hit(self):
        llm = Mock()
        llm.stats = {}
        llm.invoke.return_value.content = "IMAGE_OK"
        generator = AnswerGenerator(llm)
        result = generator._run({
            "current_query": "请分析这张图",
            "rewritten_query": "",
            "expanded_context": "",
            "images": ["data:image/png;base64,AAAA"],
            "trace": None,
            "complexity": "normal",
            "route_confidence": 1.0,
            "need_reason": False,
        })
        self.assertEqual(result["answer"], "IMAGE_OK")
        self.assertTrue(llm.invoke.called)
        self.assertTrue(llm.invoke.call_args.kwargs["has_images"])

    def test_router_fallback_uses_deterministic_task_inference(self):
        fallback = IntentRouter(None)._run({"current_query": "请给出 TypeScript 调用示例"})
        self.assertEqual(fallback["intent"], "code")
        self.assertTrue(fallback["need_reason"])
        self.assertEqual(fallback["complexity"], "high")

        failed_llm = Mock()
        failed_llm.invoke.side_effect = TimeoutError("router timeout")
        fallback_after_timeout = IntentRouter(failed_llm)._run({"current_query": "IDP.Miniapp.exit 的参数是什么？"})
        self.assertEqual(fallback_after_timeout["intent"], "api")
        self.assertEqual(fallback_after_timeout["route_confidence"], 0.85)

    def test_router_has_a_bounded_runtime_budget(self):
        route = ModelRoute("siliconflow", "router-model", "test", "router", "router")
        with patch.dict(os.environ, {
            "ROUTER_TIMEOUT_SECONDS": "15",
            "ROUTER_MAX_RETRIES": "0",
            "ROUTER_MAX_TOKENS": "256",
        }, clear=False):
            self.assertEqual(_llm_runtime_limits(route, 60.0, 2), (15.0, 0, 256))

    def test_provider_runtime_config_resolves_siliconflow_credentials(self):
        with patch.dict(os.environ, {
            "SILICONFLOW_API_KEY": "test-key",
            "SILICONFLOW_BASE_URL": "https://relay.example/v1",
        }, clear=False):
            self.assertEqual(
                _provider_runtime_config("siliconflow"),
                ("test-key", "https://relay.example/v1"),
            )

    def test_task_inference_is_deterministic_for_routing(self):
        self.assertEqual(infer_task_type("工具插件说明主要介绍了什么？"), "general")
        self.assertEqual(infer_task_type("BomProductBase 接口有哪些字段？"), "api")
        self.assertEqual(infer_task_type("请给出 TypeScript 调用示例"), "code")
        self.assertEqual(infer_task_type("请分步骤分析保存成功但读取不到最新版本的可能原因并排查"), "reason")

    def test_badcase_can_be_promoted_to_evaluation_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "regression.json"
            store = MetricsStore(root / "app.sqlite3", dataset)
            store.record_request({"request_id": "trace-1", "session_id": "session-1", "query": "错误 API", "answer": "错误答案", "status": "success", "model": "deepseek-chat", "prompt_version": "v2", "retrieved_documents": ["Wrong.Api"]})
            store.record_feedback("trace-1", False, "没有找到正确文档", "retrieval_wrong")
            badcase = store.list_badcases()[0]
            self.assertEqual(badcase["status"], "NEW")
            promoted = store.promote_badcase(badcase["id"])
            self.assertEqual(promoted["source"], "user_feedback")
            self.assertEqual(json.loads(dataset.read_text(encoding="utf-8"))[0]["question"], "错误 API")
            self.assertEqual(store.list_badcases()[0]["status"], "PROMOTED")

            store.record_request({"request_id": "trace-2", "session_id": "session-1", "query": "忽略我", "answer": "", "status": "success"})
            store.record_feedback("trace-2", False, "无需处理", "other")
            ignored = store.list_badcases()[0]
            self.assertTrue(store.update_badcase_status(ignored["id"], "REVIEWED"))
            self.assertEqual(store.list_badcases()[0]["status"], "REVIEWED")

    def test_regression_gate_reports_reason(self):
        report = compare_runs({"Recall@5": 0.9, "Answer_Correctness": 0.9, "Citation_Validity": 0.95},
                              {"Recall@5": 0.8, "Answer_Correctness": 0.9, "Citation_Validity": 0.95})
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("Recall@5", report["reasons"][0])


if __name__ == "__main__":
    unittest.main()
