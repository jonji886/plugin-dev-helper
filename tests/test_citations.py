import json
import tempfile
import unittest
from pathlib import Path

from agent.assistant import build_citations


class CitationTests(unittest.TestCase):
    def test_only_retrieved_and_indexed_documents_become_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            index_path = Path(directory) / "_index.json"
            index_path.write_text(json.dumps([
                {
                    "id": "IDP.Miniapp.exit",
                    "source": "index.d.ts",
                    "sdkVersion": "1.83.0",
                    "startLine": 10,
                    "endLine": 12,
                }
            ]), encoding="utf-8")

            citations = build_citations([
                {"id": "IDP.Miniapp.exit"},
                {"id": "not-in-index"},
            ], index_path)

        self.assertEqual(citations, [{
            "id": "IDP.Miniapp.exit",
            "source": "index.d.ts",
            "sdk_version": "1.83.0",
            "start_line": 10,
            "end_line": 12,
        }])


if __name__ == "__main__":
    unittest.main()
