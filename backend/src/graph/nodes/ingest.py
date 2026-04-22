"""Ingest node for initializing a fresh research graph run."""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

try:
    from . import emit_graph_event
    from ..state import ResearchGraphState
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import emit_graph_event
    from graph.state import ResearchGraphState


def make_ingest_request_node(_: Any) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the request-ingest node."""

    def ingest_request(state: ResearchGraphState) -> ResearchGraphState:
        topic = (state.get("topic") or "").strip()
        thread_id = state.get("thread_id") or uuid4().hex
        session_id = state.get("session_id") or thread_id
        run_id = state.get("run_id") or uuid4().hex
        ui_events = []

        emit_graph_event(
            ui_events,
            "status",
            {"message": "初始化研究流程"},
        )

        return {
            "topic": topic,
            "thread_id": thread_id,
            "session_id": session_id,
            "run_id": run_id,
            "status": "running",
            "current_stage": "ingest_request",
            "todo_items": [],
            "task_specs": [],
            "task_results": [],
            "sources_gathered": [],
            "web_research_results": [],
            "running_summary": "",
            "report_outline": None,
            "structured_report": "",
            "report_note_id": None,
            "report_note_path": None,
            "ui_events": ui_events,
            "errors": [],
        }

    return ingest_request
