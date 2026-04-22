"""State models and helpers for the LangGraph research workflow."""

from __future__ import annotations

import operator
import re
from dataclasses import replace
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

try:
    from ..models import SummaryState, TodoItem
except ImportError:  # pragma: no cover - script-mode fallback
    from models import SummaryState, TodoItem


class GraphUIEvent(TypedDict):
    """Internal graph event payload stored in state and translated for SSE."""

    name: str
    payload: dict[str, Any]


class TaskSpec(TypedDict, total=False):
    """Planner output that can be fanned out to research workers."""

    task_id: int
    title: str
    intent: str
    query: str
    dimension: str | None
    note_id: str | None
    note_path: str | None
    stream_token: str | None


class TaskExecutionResult(TypedDict, total=False):
    """Compact worker output aggregated back into the main graph state."""

    task_id: int
    title: str
    intent: str
    query: str
    dimension: str | None
    status: str
    summary: str
    sources_summary: str
    notices: list[str]
    note_id: str | None
    note_path: str | None
    backend: str
    source_count: int
    answer_excerpt: str | None
    context_preview: str
    key_findings: list[str]
    evidence_points: list[str]
    citations: list[str]
    error: str | None


class ReportOutline(TypedDict, total=False):
    """Structured guidance for the final report-writing step."""

    executive_judgment: str
    comparison_dimensions: list[str]
    section_plan: list[dict[str, Any]]
    table_plan: dict[str, Any]
    citation_focus: list[str]


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
    """End-to-end state for the orchestrator-worker research graph."""

    session_id: str
    thread_id: str
    run_id: str
    topic: str
    status: str
    current_stage: str
    todo_items: list[TodoItem]
    task_specs: list[TaskSpec]
    active_task: TaskSpec
    active_task_index: int
    task_results: Annotated[list[TaskExecutionResult], operator.add]
    sources_gathered: list[str]
    web_research_results: list[WebResearchResultRecord]
    running_summary: str
    report_outline: ReportOutline | None
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
        task_specs=[],
        task_results=[],
        sources_gathered=[],
        web_research_results=[],
        running_summary="",
        report_outline=None,
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
        report_outline=state.get("report_outline"),
        report_note_id=state.get("report_note_id"),
        report_note_path=state.get("report_note_path"),
        errors=state.get("errors", []),
    )


def task_to_spec(task: TodoItem) -> TaskSpec:
    """Convert a TodoItem into a lightweight task spec for worker fan-out."""

    return TaskSpec(
        task_id=task.id,
        title=task.title,
        intent=task.intent,
        query=task.query,
        dimension=task.dimension,
        note_id=task.note_id,
        note_path=task.note_path,
        stream_token=task.stream_token,
    )


def task_spec_to_todo_item(spec: TaskSpec) -> TodoItem:
    """Create a mutable TodoItem instance from a task spec."""

    return TodoItem(
        id=spec["task_id"],
        title=spec["title"],
        intent=spec["intent"],
        query=spec["query"],
        dimension=spec.get("dimension"),
        note_id=spec.get("note_id"),
        note_path=spec.get("note_path"),
        stream_token=spec.get("stream_token"),
    )


def merge_task_results(
    planned_tasks: list[TodoItem],
    task_results: list[TaskExecutionResult],
) -> list[TodoItem]:
    """Merge unordered worker results back into the original task order."""

    merged_tasks = {task.id: replace(task) for task in planned_tasks}

    for result in task_results:
        task_id = int(result["task_id"])
        task = merged_tasks.get(task_id)
        if task is None:
            task = TodoItem(
                id=task_id,
                title=result.get("title") or f"任务 {task_id}",
                intent=result.get("intent") or "",
                query=result.get("query") or "",
                dimension=result.get("dimension"),
            )
            merged_tasks[task_id] = task

        task.dimension = result.get("dimension") or task.dimension
        task.status = result.get("status") or task.status
        task.summary = result.get("summary") or task.summary
        task.sources_summary = result.get("sources_summary") or task.sources_summary
        task.notices = list(result.get("notices") or task.notices or [])
        task.note_id = result.get("note_id") or task.note_id
        task.note_path = result.get("note_path") or task.note_path

    return [merged_tasks[task_id] for task_id in sorted(merged_tasks)]


def compact_text(text: str | None, *, max_chars: int = 2000) -> str:
    """Trim large text fields before putting them into checkpoints."""

    if not text:
        return ""
    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    return f"{trimmed[:max_chars]}... [truncated]"


def extract_key_findings(summary_text: str, *, max_items: int = 5) -> list[str]:
    """Extract short findings from a task summary for report aggregation."""

    if not summary_text:
        return []

    findings: list[str] = []
    for line in summary_text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.startswith(("###", "##")):
            continue
        if re.match(r"^[-*]\s+", candidate):
            findings.append(re.sub(r"^[-*]\s+", "", candidate))
        elif re.match(r"^\d+[.)、]\s+", candidate):
            findings.append(re.sub(r"^\d+[.)、]\s+", "", candidate))

        if len(findings) >= max_items:
            break

    if findings:
        return findings

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", summary_text)
        if paragraph.strip()
    ]
    return paragraphs[:max_items]


def extract_citations(sources_summary: str, *, max_items: int = 6) -> list[str]:
    """Extract source citations from the formatted source summary text."""

    citations: list[str] = []
    for line in sources_summary.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if "http://" in candidate or "https://" in candidate:
            citations.append(candidate.lstrip("- ").strip())
        if len(citations) >= max_items:
            break
    return citations
