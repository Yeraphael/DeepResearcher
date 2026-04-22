from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from application.event_translator import GraphEventTranslator
from application.research_runner import GraphRuntime, ResearchRunner
from config import Configuration
from graph.nodes.planner import make_plan_tasks_node
from graph.nodes.report import make_build_report_outline_node, make_compile_report_node
from graph.state import build_initial_graph_state
from models import SummaryState, TodoItem
from services.reporter import ReportingService
from services.tool_events import ToolCallTracker


class FakePlanningService:
    def plan_todo_list(self, state):
        return [
            TodoItem(
                id=1,
                title="背景概览",
                intent="梳理主题背景",
                query=f"{state.research_topic} 背景",
                dimension="背景概览",
            ),
            TodoItem(
                id=2,
                title="关键趋势",
                intent="梳理主要趋势",
                query=f"{state.research_topic} 趋势",
                dimension="趋势比较",
            ),
        ]

    def create_fallback_task(self, state):
        return TodoItem(
            id=1,
            title="默认任务",
            intent="默认任务意图",
            query=state.research_topic,
            dimension="背景概览",
        )


class FakeSummarizationService:
    def summarize_task(self, state, task, context):
        return f"Summary for {task.title}: {context}"

    def stream_task_summary(self, state, task, context):
        parts = [f"{task.title} summary chunk 1. ", "chunk 2."]

        def iterator():
            yield from parts

        def getter():
            return "".join(parts)

        return iterator(), getter


class FakeReportingService:
    def build_report_outline(self, state, *, task_results=None):
        return {
            "executive_judgment": "AI agents are becoming the default automation layer.",
            "comparison_dimensions": ["背景概览", "趋势比较"],
            "section_plan": [
                {"heading": "背景概览", "purpose": "Explain the topic", "task_ids": [1]},
                {"heading": "趋势比较", "purpose": "Compare key trends", "task_ids": [2]},
            ],
            "table_plan": {
                "title": "Comparison",
                "columns": ["维度", "领先者/方案", "证据", "备注"],
            },
            "citation_focus": ["任务 1", "任务 2"],
        }

    def generate_report(self, state, *, report_outline=None, task_results=None):
        completed = ", ".join(task.title for task in state.todo_items)
        dimensions = ", ".join((report_outline or {}).get("comparison_dimensions", []))
        return (
            "# Report\n\n"
            f"Topic: {state.research_topic}\n\n"
            f"Dimensions: {dimensions}\n\n"
            f"Tasks: {completed}"
        )


class TimeoutReportAgent:
    def __init__(self) -> None:
        self.clear_count = 0

    def run(self, prompt):
        raise RuntimeError("Request timed out.")

    def clear_history(self):
        self.clear_count += 1


def build_fake_runtime(config: Configuration) -> GraphRuntime:
    return GraphRuntime(
        config=config,
        planner=FakePlanningService(),
        summarizer=FakeSummarizationService(),
        reporting=FakeReportingService(),
        tool_tracker=ToolCallTracker(None),
        note_tool=None,
    )


def test_plan_tasks_node_outputs_valid_todos(workspace_tmp_path):
    config = Configuration(
        enable_notes=False,
        notes_workspace=str(workspace_tmp_path / "notes"),
        langgraph_checkpoint_path=str(workspace_tmp_path / "checkpoints.sqlite"),
    )
    runtime = build_fake_runtime(config)
    node = make_plan_tasks_node(runtime)
    state = build_initial_graph_state(
        topic="AI agents",
        session_id="session-1",
        thread_id="thread-1",
        run_id="run-1",
    )

    result = node(state)

    assert len(result["todo_items"]) == 2
    assert result["todo_items"][0].query == "AI agents 背景"
    todo_list_events = [event for event in result["ui_events"] if event["name"] == "todo_list"]
    assert todo_list_events
    assert todo_list_events[0]["payload"]["tasks"][0]["title"] == "背景概览"
    assert result["task_specs"][0]["dimension"] == "背景概览"


def test_build_report_outline_node_generates_outline(workspace_tmp_path):
    config = Configuration(
        enable_notes=False,
        notes_workspace=str(workspace_tmp_path / "notes"),
        langgraph_checkpoint_path=str(workspace_tmp_path / "checkpoints.sqlite"),
    )
    runtime = build_fake_runtime(config)
    node = make_build_report_outline_node(runtime)
    state = build_initial_graph_state(
        topic="AI agents",
        session_id="session-1",
        thread_id="thread-1",
        run_id="run-1",
    )
    state["todo_items"] = [
        TodoItem(
            id=1,
            title="背景概览",
            intent="梳理背景",
            query="AI agents 背景",
            dimension="背景概览",
            status="completed",
            summary="Task summary",
            sources_summary="* source : https://example.com",
        )
    ]
    state["task_results"] = [
        {
            "task_id": 1,
            "title": "背景概览",
            "dimension": "背景概览",
            "summary": "Task summary",
            "citations": ["https://example.com"],
            "key_findings": ["Agents are growing fast"],
        }
    ]

    result = node(state)

    assert result["report_outline"]["comparison_dimensions"] == ["背景概览", "趋势比较"]


def test_compile_report_node_generates_report(workspace_tmp_path):
    config = Configuration(
        enable_notes=False,
        notes_workspace=str(workspace_tmp_path / "notes"),
        langgraph_checkpoint_path=str(workspace_tmp_path / "checkpoints.sqlite"),
    )
    runtime = build_fake_runtime(config)
    node = make_compile_report_node(runtime)
    state = build_initial_graph_state(
        topic="AI agents",
        session_id="session-1",
        thread_id="thread-1",
        run_id="run-1",
    )
    state["todo_items"] = [
        TodoItem(
            id=1,
            title="背景概览",
            intent="梳理背景",
            query="AI agents 背景",
            dimension="背景概览",
            status="completed",
            summary="Task summary",
            sources_summary="* source : https://example.com",
        )
    ]
    state["running_summary"] = "Task summary"
    state["report_outline"] = {
        "comparison_dimensions": ["背景概览"],
    }
    state["task_results"] = [
        {
            "task_id": 1,
            "title": "背景概览",
            "dimension": "背景概览",
            "summary": "Task summary",
        }
    ]

    result = node(state)

    assert result["structured_report"].startswith("# Report")
    assert "背景概览" in result["structured_report"]
    assert "Dimensions: 背景概览" in result["structured_report"]


def test_reporting_service_falls_back_when_report_llm_times_out(workspace_tmp_path):
    config = Configuration(
        enable_notes=False,
        notes_workspace=str(workspace_tmp_path / "notes"),
        langgraph_checkpoint_path=str(workspace_tmp_path / "checkpoints.sqlite"),
    )
    agent = TimeoutReportAgent()
    service = ReportingService(agent, config)
    state = SummaryState(
        research_topic="AI vendor comparison",
        todo_items=[
            TodoItem(
                id=1,
                title="Model capability",
                intent="Compare model capability",
                query="AI vendor model capability 2026",
                dimension="Model",
                status="completed",
                summary="- OpenAI leads on frontier multimodal capability.\n- Google remains strong in research breadth.",
                sources_summary="- OpenAI release notes: https://example.com/openai\n- Google Gemini update: https://example.com/google",
            )
        ],
    )
    task_results = [
        {
            "task_id": 1,
            "title": "Model capability",
            "dimension": "Model",
            "summary": "OpenAI leads on frontier multimodal capability.",
            "key_findings": ["OpenAI leads on frontier multimodal capability."],
            "evidence_points": ["Google remains strong in research breadth."],
            "citations": ["https://example.com/openai"],
        }
    ]

    outline = service.build_report_outline(state, task_results=task_results)
    report = service.generate_report(
        state,
        report_outline=outline,
        task_results=task_results,
    )

    assert "兜底报告" in report
    assert "Request timed out" in report
    assert "https://example.com/openai" in report
    assert agent.clear_count == 2


def test_event_translator_maps_custom_graph_events():
    translator = GraphEventTranslator()
    parts = translator.translate_stream_part(
        {
            "type": "custom",
            "data": {
                "name": "task_status",
                "payload": {
                    "task_id": 1,
                    "status": "completed",
                    "summary": "done",
                },
            },
        }
    )

    assert parts == [
        {
            "type": "task_status",
            "task_id": 1,
            "status": "completed",
            "summary": "done",
        }
    ]


@pytest.fixture
def mocked_runner(monkeypatch, workspace_tmp_path):
    checkpoint_path = workspace_tmp_path / "langgraph.sqlite"
    notes_workspace = workspace_tmp_path / "notes"
    monkeypatch.setenv("ENABLE_NOTES", "false")
    monkeypatch.setenv("NOTES_WORKSPACE", str(notes_workspace))
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_PATH", str(checkpoint_path))

    def fake_runtime_builder(self):
        return build_fake_runtime(self.config)

    def fake_dispatch_search(query, config, loop_count):
        return (
            {
                "backend": "mock",
                "answer": "mock answer",
                "results": [
                    {
                        "title": f"{query} source",
                        "url": f"https://example.com/{loop_count + 1}",
                        "content": f"content for {query}",
                        "raw_content": f"full page content for {query}",
                    }
                ],
            },
            [],
            "mock answer",
            "mock",
        )

    monkeypatch.setattr(ResearchRunner, "_build_runtime", fake_runtime_builder)
    monkeypatch.setattr("graph.nodes.search.dispatch_search", fake_dispatch_search)

    return {
        "notes_workspace": notes_workspace,
    }


def test_research_endpoint_runs_with_mocked_graph(mocked_runner):
    from main import create_app

    client = TestClient(create_app())
    response = client.post("/research", json={"topic": "AI agents"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_markdown"].startswith("# Report")
    assert len(payload["todo_items"]) == 2
    saved_reports = list(mocked_runner["notes_workspace"].glob("report_*.md"))
    assert saved_reports


def test_research_stream_endpoint_emits_full_sequence(mocked_runner):
    from main import create_app

    client = TestClient(create_app())
    events: list[dict[str, object]] = []

    with client.stream("POST", "/research/stream", json={"topic": "AI agents"}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            events.append(payload)

    event_types = [event["type"] for event in events]
    assert events[0] == {
        "type": "status",
        "message": "后端已接收研究请求，正在启动 LangGraph 工作流",
    }
    assert "todo_list" in event_types
    assert "sources" in event_types
    assert "task_summary_chunk" in event_types
    assert "final_report" in event_types
    assert event_types[-1] == "done"
