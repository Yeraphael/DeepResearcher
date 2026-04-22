"""Helpers for task summarization inside the research graph."""

from __future__ import annotations

from collections.abc import Iterator

try:
    from . import convert_tool_event, emit_graph_event
    from ..state import ResearchGraphState, to_summary_state
    from ...models import TodoItem
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import convert_tool_event, emit_graph_event
    from graph.state import ResearchGraphState, to_summary_state
    from models import TodoItem


def build_running_summary(tasks: list[TodoItem]) -> str:
    """Build a compact cumulative summary from completed task outputs."""

    completed_blocks: list[str] = []
    for task in tasks:
        if not task.summary:
            continue
        completed_blocks.append(
            f"## 任务 {task.id}: {task.title}\n"
            f"- 比较维度：{task.dimension or task.title}\n"
            f"- 任务意图：{task.intent}\n"
            f"- 搜索查询：{task.query}\n"
            f"- 当前状态：{task.status}\n"
            f"{task.summary.strip()}"
        )

    return "\n\n".join(completed_blocks)


def stream_task_summary(
    *,
    runtime: object,
    state: ResearchGraphState,
    task: TodoItem,
    context: str,
    ui_events: list[dict],
) -> str:
    """Stream task summary chunks and return the final normalized summary text."""

    legacy_state = to_summary_state(state)
    summary_stream, summary_getter = runtime.summarizer.stream_task_summary(
        legacy_state,
        task,
        context,
    )

    for tool_event in runtime.tool_tracker.drain(legacy_state):
        internal_event = convert_tool_event(tool_event)
        emit_graph_event(
            ui_events,
            internal_event["name"],
            internal_event["payload"],
            persist=False,
        )

    try:
        for chunk in _safe_iter(summary_stream):
            if chunk:
                emit_graph_event(
                    ui_events,
                    "task_summary_chunk",
                    {
                        "task_id": task.id,
                        "content": chunk,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                    persist=False,
                )

            for tool_event in runtime.tool_tracker.drain(legacy_state):
                internal_event = convert_tool_event(tool_event)
                emit_graph_event(
                    ui_events,
                    internal_event["name"],
                    internal_event["payload"],
                    persist=False,
                )
    finally:
        for tool_event in runtime.tool_tracker.drain(legacy_state):
            internal_event = convert_tool_event(tool_event)
            emit_graph_event(
                ui_events,
                internal_event["name"],
                internal_event["payload"],
                persist=False,
            )

    summary_text = summary_getter().strip()
    return summary_text or "暂无可用信息"


def _safe_iter(stream: Iterator[str]) -> Iterator[str]:
    """Yield a possibly lazy iterator safely."""

    for chunk in stream:
        yield chunk
