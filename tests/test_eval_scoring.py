import unittest
import time

from eval.ab_eval import EvaluationTimeout, _hard_timeout
from eval.scoring import score_answer
from eval.ab_eval import _citation_validity


class EvalScoringTests(unittest.TestCase):
    def test_real_eval_has_a_hard_deadline_for_blocked_provider_reads(self):
        with self.assertRaises(EvaluationTimeout):
            with _hard_timeout(0.01):
                time.sleep(0.05)

    def test_answer_requires_half_of_the_expected_keywords_by_default(self):
        case = {"expected_answer": "调用 IDP.Design.save，返回 Promise<void>"}
        self.assertEqual(score_answer("调用 IDP.Design.save。", case), (True, 0.5))
        self.assertEqual(score_answer("这是一个设计接口。", case), (False, 0.0))

    def test_abstention_case_accepts_explicit_unknown_answer(self):
        case = {"expected_behavior": "abstain"}
        self.assertEqual(score_answer("知识库中没有找到相关信息。", case), (True, 1.0))
        self.assertEqual(score_answer("无法准确回答，知识库信息不足。", case), (True, 1.0))
        self.assertEqual(score_answer("根据知识库内容无法回答。", case), (True, 1.0))
        self.assertEqual(score_answer("可以调用 IDP.Unknown.run()。", case), (False, 0.0))

    def test_scoring_accepts_natural_language_and_markdown_variants(self):
        case = {"expected_answer": "调用 IDP.Platform.getAppMode() 获取当前应用模式"}
        answer = "`IDP.Platform.getAppMode` 的作用是获取当前应用模式。"
        self.assertEqual(score_answer(answer, case), (True, 1.0))

    def test_abstention_accepts_traceable_evidence_citations(self):
        case = {"expected_behavior": "abstain"}
        result = {"citations": [{"id": "doc-1", "source": "index.d.ts", "sdk_version": "v1.0"}]}
        index = {"doc-1": {"source": "index.d.ts", "sdkVersion": "v1.0"}}
        self.assertTrue(_citation_validity(result, index, case))


if __name__ == "__main__":
    unittest.main()
