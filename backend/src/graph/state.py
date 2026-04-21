"""State models and helpers for the LangGraph research workflow."""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field

try:
    from ..models import SummaryState, TodoItem
except ImportError:  # pragma: no cover - script-mode fallback
    from models import SummaryState, TodoItem


class GraphUIEvent(TypedDict):
    """Internal graph event payload stored in state and translated for SSE."""

    name: str
    payload: dict[str, Any]


class WebResearchResultRecord(BaseModel):
    """Compact task-level research snapshot safe to checkpoint in SQLite."""

    task_id: int
    title: str
    query: str
    backend: str
    source_count: int = Field(default=0)
    sources_summary: str = Field(default="")
    answer_excerpt: str | None = Field(default=None)
    context_preview: str = Field(default="")


class ResearchGraphState(TypedDict, total=False):
    """End-to-end state for the MVP LangGraph workflow."""

    session_id: str
    thread_id: str
    run_id: str
    topic: str
    status: str
    current_stage: str
    todo_items: list[TodoItem]
    sources_gathered: list[str]
    web_research_results: list[WebResearchResultRecord]
    running_summary: str
    structured_report: str
    report_note_id: str | None
    report_note_path: str | None
    ui_events: list[GraphUIEvent]
    errors: list[str]


def build_initial_graph_state(
    *,
    topic: str,
    session_id: str,
    thread_id: str,
    run_id: str,
) -> ResearchGraphState:
    """Create a fresh graph input payload for a new research run."""

    return ResearchGraphState(
        session_id=session_id,
        thread_id=thread_id,
        run_id=run_id,
        topic=topic,
        status="pending",
        current_stage="pending",
        todo_items=[],
        sources_gathered=[],
        web_research_results=[],
        running_summary="",
        structured_report="",
        report_note_id=None,
        report_note_path=None,
        ui_events=[],
        errors=[],
    )


def to_summary_state(state: ResearchGraphState) -> SummaryState:
    """Adapt graph state to the legacy SummaryState expected by services."""

    return SummaryState(
        research_topic=state.get("topic"),
        session_id=state.get("session_id"),
        thread_id=state.get("thread_id"),
        run_id=state.get("run_id"),
        web_research_results=state.get("web_research_results", []),
        sources_gathered=state.get("sources_gathered", []),
        research_loop_count=len(state.get("web_research_results", [])),
        status=state.get("status"),
        current_stage=state.get("current_stage"),
        running_summary=state.get("running_summary"),
        todo_items=state.get("todo_items", []),
        structured_report=state.get("structured_report"),
        report_note_id=state.get("report_note_id"),
        report_note_path=state.get("report_note_path"),
        errors=state.get("errors", []),
    )


def compact_text(text: str | None, *, max_chars: int = 2000) -> str:
    """Trim large text fields before putting them into checkpoints."""

    if not text:
        return ""
    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    return f"{trimmed[:max_chars]}... [truncated]"
