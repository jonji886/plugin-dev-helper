import tempfile
import unittest
from pathlib import Path

from agent.assistant import SessionManager


class SessionPersistenceTests(unittest.TestCase):
    def test_sqlite_session_history_survives_manager_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "app.sqlite3"
            manager = SessionManager(str(database_path))
            session_id = manager.create_session()
            manager.add_message(session_id, "user", "如何保存方案？", request_id="request-1")
            manager.add_message(session_id, "assistant", "调用 IDP.Design.save()", citations=[{
                "id": "IDP.Design.save",
                "source": "index.d.ts",
                "sdk_version": "1.83.0",
                "start_line": 4142,
                "end_line": 4142,
            }], request_id="request-1")

            restored_manager = SessionManager(str(database_path))
            history = restored_manager.get_history(session_id)

        self.assertEqual([message["role"] for message in history], ["user", "assistant"])
        self.assertEqual(history[1]["citations"][0]["id"], "IDP.Design.save")


if __name__ == "__main__":
    unittest.main()
