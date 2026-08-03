import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

import app.main as main
from app.metrics_store import MetricsStore


class FakeSessionManager:
    def __init__(self):
        self.sessions = {"a1b2c3d4": [{"role": "user", "content": "旧消息"}]}

    def get_history(self, session_id):
        return self.sessions.get(session_id, [])

    def get_all_sessions(self):
        return [{"id": session_id, "message_count": len(messages), "last_message": ""}
                for session_id, messages in self.sessions.items()]

    def delete_session(self, session_id):
        self.sessions.pop(session_id, None)

    def clear_all_sessions(self):
        self.sessions.clear()


class FakeAgentRunner:
    def __init__(self):
        self.session_manager = FakeSessionManager()

    def chat(self, query, session_id=None, request_id=None):
        return {
            "answer": query,
            "session_id": session_id or "a1b2c3d4",
            "intent": "general",
            "retrieved_count": 1,
            "citations": [{
                "id": "IDP.Miniapp.exit",
                "source": "index.d.ts",
                "sdk_version": "1.83.0",
                "start_line": 10,
                "end_line": 12,
            }],
        }


class ApiTests(unittest.TestCase):
    def test_chat_returns_structured_citations_and_clear_all_removes_sessions(self):
        previous_runner = main.agent_runner
        previous_metrics_store = main.metrics_store
        main.agent_runner = FakeAgentRunner()

        with tempfile.TemporaryDirectory() as directory:
            main.metrics_store = MetricsStore(Path(directory) / "app.sqlite3")

            async def request():
                transport = httpx.ASGITransport(app=main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    chat_response = await client.post("/api/chat", json={
                        "query": "如何退出？", "session_id": "a1b2c3d4"
                    })
                    feedback_response = await client.post("/api/chat/feedback", json={
                        "request_id": chat_response.json()["request_id"], "helpful": True
                    })
                    metrics_response = await client.get("/api/metrics")
                    clear_response = await client.delete("/api/chat/history")
                    invalid_response = await client.get("/api/chat/history?session_id=invalid")
                    return chat_response, feedback_response, metrics_response, clear_response, invalid_response

            try:
                chat_response, feedback_response, metrics_response, clear_response, invalid_response = asyncio.run(request())
                self.assertEqual(chat_response.status_code, 200)
                self.assertEqual(chat_response.json()["citations"][0]["id"], "IDP.Miniapp.exit")
                self.assertEqual(feedback_response.status_code, 204)
                self.assertEqual(feedback_response.content, b"")
                self.assertEqual(metrics_response.json()["helpful_rate"], 1.0)
                self.assertEqual(clear_response.status_code, 200)
                self.assertEqual(main.agent_runner.session_manager.sessions, {})
                self.assertEqual(invalid_response.status_code, 400)
            finally:
                main.agent_runner = previous_runner
                main.metrics_store = previous_metrics_store


if __name__ == "__main__":
    unittest.main()
