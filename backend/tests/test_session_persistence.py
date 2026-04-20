from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BACKEND_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def load_backend_modules(database_path: str, agent_class):
    os.environ["DATABASE_PATH"] = database_path

    agent_module = types.ModuleType("agent")
    agent_module.DeepResearchAgent = agent_class
    sys.modules["agent"] = agent_module

    config_module = importlib.import_module("config")
    importlib.reload(config_module)

    session_store_module = importlib.import_module("session_store")
    importlib.reload(session_store_module)

    main_module = importlib.import_module("main")
    return importlib.reload(main_module)


class StubDeepResearchAgent:
    def __init__(self, config):
        self.config = config

    def run_stream(self, topic: str):
        normalized = topic.strip()
        yield {"type": "status", "message": "初始化研究流程"}

        if "失败" in normalized:
            raise RuntimeError(f"模拟研究失败：{normalized}")

        task_prefix = normalized[:8] or "研究任务"
        yield {
            "type": "todo_list",
            "step": 0,
            "tasks": [
                {
                    "id": 1,
                    "title": f"{task_prefix} - 任务一",
                    "intent": "收集背景信息",
                    "query": f"{normalized} 背景",
                    "status": "pending",
                    "note_id": "note_task_1",
                    "note_path": "notes/note_task_1.md",
                },
                {
                    "id": 2,
                    "title": f"{task_prefix} - 任务二",
                    "intent": "形成结论",
                    "query": f"{normalized} 结论",
                    "status": "pending",
                    "note_id": "note_task_2",
                    "note_path": "notes/note_task_2.md",
                },
            ],
        }

        yield {
            "type": "task_status",
            "task_id": 1,
            "step": 1,
            "status": "in_progress",
            "title": f"{task_prefix} - 任务一",
            "intent": "收集背景信息",
            "note_id": "note_task_1",
            "note_path": "notes/note_task_1.md",
        }
        yield {
            "type": "sources",
            "task_id": 1,
            "step": 1,
            "latest_sources": f"- 来源A | https://example.com/{normalized}/a | 背景信息",
            "backend": "duckduckgo",
            "note_id": "note_task_1",
            "note_path": "notes/note_task_1.md",
        }
        yield {
            "type": "task_summary_chunk",
            "task_id": 1,
            "step": 1,
            "content": f"{normalized} 的背景信息已整理。",
            "note_id": "note_task_1",
            "note_path": "notes/note_task_1.md",
        }
        yield {
            "type": "tool_call",
            "task_id": 1,
            "step": 1,
            "event_id": 101,
            "agent": "研究员",
            "tool": "search",
            "parameters": {"query": f"{normalized} 背景"},
            "result": "返回 3 条结果",
            "note_id": "note_task_1",
            "note_path": "notes/note_task_1.md",
        }
        yield {
            "type": "task_status",
            "task_id": 1,
            "step": 1,
            "status": "completed",
            "title": f"{task_prefix} - 任务一",
            "intent": "收集背景信息",
            "summary": f"{normalized} 的背景信息已整理。",
            "sources_summary": f"- 来源A | https://example.com/{normalized}/a | 背景信息",
            "note_id": "note_task_1",
            "note_path": "notes/note_task_1.md",
        }

        yield {
            "type": "task_status",
            "task_id": 2,
            "step": 2,
            "status": "in_progress",
            "title": f"{task_prefix} - 任务二",
            "intent": "形成结论",
            "note_id": "note_task_2",
            "note_path": "notes/note_task_2.md",
        }
        yield {
            "type": "task_summary_chunk",
            "task_id": 2,
            "step": 2,
            "content": f"{normalized} 的结论已经形成。",
            "note_id": "note_task_2",
            "note_path": "notes/note_task_2.md",
        }
        yield {
            "type": "task_status",
            "task_id": 2,
            "step": 2,
            "status": "completed",
            "title": f"{task_prefix} - 任务二",
            "intent": "形成结论",
            "summary": f"{normalized} 的结论已经形成。",
            "sources_summary": "",
            "note_id": "note_task_2",
            "note_path": "notes/note_task_2.md",
        }

        yield {
            "type": "report_note",
            "note_id": "note_report_1",
            "note_path": "notes/note_report_1.md",
        }
        yield {
            "type": "final_report",
            "report": f"# {normalized}\n\n这是关于 {normalized} 的最终报告。",
            "note_id": "note_report_1",
            "note_path": "notes/note_report_1.md",
        }
        yield {"type": "done"}


class SessionPersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self._tempdir.name) / "research.db")
        self.main_module = load_backend_modules(
            self.database_path,
            StubDeepResearchAgent,
        )
        self.client = TestClient(self.main_module.create_app())

    def tearDown(self):
        self.client.close()
        self._tempdir.cleanup()

    def _create_session(self):
        response = self.client.post("/api/research/sessions", json={})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def _run_session(self, session_id: int, topic: str):
        response = self.client.post(
            f"/api/research/sessions/{session_id}/run/stream",
            json={"topic": topic},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("data:", response.text)
        return response

    def test_sessions_persist_across_restart(self):
        session_a = self._create_session()
        session_b = self._create_session()

        self._run_session(session_a["id"], "AI 厂商综合能力对比")
        self._run_session(session_b["id"], "多模态模型趋势")

        list_response = self.client.get("/api/research/sessions")
        self.assertEqual(list_response.status_code, 200)
        sessions = list_response.json()
        self.assertEqual(len(sessions), 2)

        listed_topics = {item["topic"] for item in sessions}
        self.assertEqual(listed_topics, {"AI 厂商综合能力对比", "多模态模型趋势"})

        detail_response = self.client.get(f"/api/research/sessions/{session_a['id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["total_tasks"], 2)
        self.assertEqual(detail["completed_tasks"], 2)
        self.assertGreaterEqual(len(detail["tasks"]), 2)
        self.assertTrue(detail["report_markdown"].startswith("# AI 厂商综合能力对比"))
        self.assertTrue(detail["progress_logs"])
        self.assertTrue(detail["tool_calls"])

        restarted_main = load_backend_modules(
            self.database_path,
            StubDeepResearchAgent,
        )
        restarted_client = TestClient(restarted_main.create_app())
        try:
            restarted_list = restarted_client.get("/api/research/sessions")
            self.assertEqual(restarted_list.status_code, 200)
            restarted_sessions = restarted_list.json()
            self.assertEqual(len(restarted_sessions), 2)

            restarted_detail = restarted_client.get(
                f"/api/research/sessions/{session_b['id']}"
            )
            self.assertEqual(restarted_detail.status_code, 200)
            restarted_payload = restarted_detail.json()
            self.assertEqual(restarted_payload["topic"], "多模态模型趋势")
            self.assertEqual(restarted_payload["status"], "completed")
            self.assertTrue(restarted_payload["report_markdown"])
        finally:
            restarted_client.close()

    def test_failed_session_is_still_visible(self):
        session = self._create_session()
        response = self._run_session(session["id"], "模拟失败案例")

        self.assertIn('"type": "error"', response.text)

        detail_response = self.client.get(f"/api/research/sessions/{session['id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["status"], "failed")
        self.assertIn("模拟研究失败", detail["error_message"])

        list_response = self.client.get("/api/research/sessions")
        sessions = list_response.json()
        matched = next(item for item in sessions if item["id"] == session["id"])
        self.assertEqual(matched["status"], "failed")
        self.assertEqual(matched["topic"], "模拟失败案例")


if __name__ == "__main__":
    unittest.main()
