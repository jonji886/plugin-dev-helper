import tempfile
import unittest
from pathlib import Path

from app.prompt_registry import PromptRegistry


class PromptRegistryTests(unittest.TestCase):
    def test_loads_content_and_metadata_for_a_version(self):
        registry = PromptRegistry()
        content = registry.get("developer_qa", "v1")
        metadata = registry.metadata("developer_qa", "v1")

        self.assertIn("知识库", content)
        self.assertEqual(metadata["prompt_name"], "developer_qa")
        self.assertEqual(metadata["prompt_version"], "v1")
        self.assertEqual(metadata["status"], "active")
        self.assertTrue(metadata["created_at"])
        self.assertTrue(metadata["description"])

    def test_missing_version_fails_explicitly(self):
        with self.assertRaises(FileNotFoundError):
            PromptRegistry().get("developer_qa", "v99")

    def test_missing_manifest_is_backward_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "prompts"
            (root / "demo").mkdir(parents=True)
            (root / "demo" / "v1.md").write_text("demo", encoding="utf-8")
            metadata = PromptRegistry(root).metadata("demo", "v1")

        self.assertEqual(metadata["prompt_name"], "demo")
        self.assertEqual(metadata["prompt_version"], "v1")
        self.assertEqual(metadata["status"], "unknown")

    def test_lists_reviewable_versions(self):
        versions = PromptRegistry().list_versions("developer_qa")
        self.assertEqual([item["prompt_version"] for item in versions], ["v1", "v2"])
        self.assertEqual(versions[1]["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
