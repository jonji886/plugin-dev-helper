import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_builder.builder import KnowledgeBuilder
from scripts import sync_rag_docs
from sdk_parser.models import Symbol


class KnowledgeIndexTests(unittest.TestCase):
    def test_sdk_build_preserves_existing_rag_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            rag_entry = {
                "id": "rag.guide",
                "namespace": "docs.rag",
                "mdFile": "rag_guide.md",
            }
            (output_dir / "_index.json").write_text(
                json.dumps([rag_entry]), encoding="utf-8"
            )

            index = KnowledgeBuilder(str(output_dir)).build([
                Symbol(
                    id="IDP.exit",
                    name="exit",
                    symbol_type="function",
                    namespace_path=["IDP"],
                    source="index.d.ts",
                )
            ])

            self.assertEqual({entry["id"] for entry in index}, {"IDP.exit", "rag.guide"})
            sdk_entry = next(entry for entry in index if entry["id"] == "IDP.exit")
            self.assertEqual(len(sdk_entry["contentHash"]), 64)

    def test_unchanged_rag_sync_does_not_request_reindex(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs_dir = root / "docs" / "rag"
            knowledge_dir = root / "data" / "knowledge"
            docs_dir.mkdir(parents=True)
            (docs_dir / "工具说明.md").write_text("# 工具说明\n\n用于测试。\n", encoding="utf-8")

            with patch.object(sync_rag_docs, "ROOT", root), \
                 patch.object(sync_rag_docs, "DOCS_DIR", docs_dir), \
                 patch.object(sync_rag_docs, "KNOWLEDGE_DIR", knowledge_dir), \
                 patch.object(sync_rag_docs, "INDEX_PATH", knowledge_dir / "_index.json"):
                self.assertTrue(sync_rag_docs.sync_docs(rebuild_index=False))
                self.assertFalse(sync_rag_docs.sync_docs(rebuild_index=False))


if __name__ == "__main__":
    unittest.main()
