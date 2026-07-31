import unittest

from vector_store import VectorStore


class HybridRetrievalTests(unittest.TestCase):
    def test_short_jsdoc_description_matches_a_natural_language_question(self):
        save_entry = {
            "id": "IDP.Design.save",
            "name": "save",
            "namespace": "IDP.Design",
            "aliases": ["save", "IDP.Design.save"],
            "description": "保存方案",
        }
        unrelated_entry = {
            "id": "IDP.UI.hideAll",
            "name": "hideAll",
            "namespace": "IDP.UI",
            "aliases": ["hideAll", "IDP.UI.hideAll"],
            "description": "隐藏所有界面",
        }

        question = "保存设计方案接口是哪个"
        self.assertGreater(
            VectorStore.keyword_score(question, save_entry),
            VectorStore.keyword_score(question, unrelated_entry),
        )


if __name__ == "__main__":
    unittest.main()
