import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.assistant import AgentRunner
from app.config import get_settings


class RuntimeConfigurationTests(unittest.TestCase):
    def test_settings_exposes_all_runtime_data_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            with patch.dict(os.environ, {
                "DATA_DIR": str(data_dir),
                "CHROMA_PATH": str(data_dir / "custom-chroma"),
                "KNOWLEDGE_PATH": str(data_dir / "custom-knowledge"),
                "GRAPH_PATH": str(data_dir / "custom-graph.json"),
            }, clear=False):
                settings = get_settings()

        self.assertEqual(settings.chroma_path, data_dir / "custom-chroma")
        self.assertEqual(settings.knowledge_path, data_dir / "custom-knowledge")
        self.assertEqual(settings.graph_path, data_dir / "custom-graph.json")

    @patch("agent.assistant.build_agent")
    def test_agent_runner_uses_configured_paths_for_agent_and_citations(self, build_agent):
        fake_agent = Mock()
        fake_agent.invoke.return_value = {
            "answer": "调用 IDP.Design.save()",
            "intent": "api",
            "retrieved_docs": [{"id": "IDP.Design.save"}],
        }
        build_agent.return_value = fake_agent

        with tempfile.TemporaryDirectory() as directory:
            knowledge_path = Path(directory) / "knowledge"
            knowledge_path.mkdir()
            (knowledge_path / "_index.json").write_text(json.dumps([{
                "id": "IDP.Design.save",
                "source": "index.d.ts",
                "sdkVersion": "1.83.0",
                "startLine": 4138,
                "endLine": 4142,
            }]), encoding="utf-8")

            runner = AgentRunner(
                chroma_path="/tmp/custom-chroma",
                knowledge_path=str(knowledge_path),
                graph_path="/tmp/custom-graph.json",
            )
            result = runner.chat("保存设计方案接口是哪个？", request_id="request-1")

        build_agent.assert_called_once_with(
            top_k=5,
            timeout_seconds=30.0,
            max_retries=2,
            chroma_path="/tmp/custom-chroma",
            knowledge_path=str(knowledge_path),
            graph_path="/tmp/custom-graph.json",
        )
        self.assertEqual(result["citations"][0]["id"], "IDP.Design.save")


if __name__ == "__main__":
    unittest.main()
