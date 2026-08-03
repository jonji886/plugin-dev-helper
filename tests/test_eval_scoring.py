import unittest

from eval.scoring import score_answer


class EvalScoringTests(unittest.TestCase):
    def test_answer_requires_half_of_the_expected_keywords_by_default(self):
        case = {"expected_answer": "调用 IDP.Design.save，返回 Promise<void>"}
        self.assertEqual(score_answer("调用 IDP.Design.save。", case), (True, 0.5))
        self.assertEqual(score_answer("这是一个设计接口。", case), (False, 0.0))

    def test_abstention_case_accepts_explicit_unknown_answer(self):
        case = {"expected_behavior": "abstain"}
        self.assertEqual(score_answer("知识库中没有找到相关信息。", case), (True, 1.0))
        self.assertEqual(score_answer("可以调用 IDP.Unknown.run()。", case), (False, 0.0))


if __name__ == "__main__":
    unittest.main()
