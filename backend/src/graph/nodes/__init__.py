"""Shared helpers for LangGraph research nodes."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

try:
    from ..state import GraphUIEvent
    from ...models import TodoItem
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.state import GraphUIEvent
    from models import TodoItem


def serialize_task(task: TodoItem) -> dict[str, Any]:
    """Convert a TodoItem into the frontend-compatible task payload."""

    return {
        "id": task.id,
        "title": task.title,
        "intent": task.intent,
        "query": task.query,
        "dimension": task.dimension,
        "status": task.status,
        "summary": task.summary,
        "sources_summary": task.sources_summary,
        "note_id": task.note_id,
        "note_path": task.note_path,
        "stream_token": task.stream_token,
    }


def build_graph_event(name: str, payload: dict[str, Any]) -> GraphUIEvent:
    """Create a normalized internal graph event."""

    return {"name": name, "payload": payload}


def emit_graph_event(
    ui_events: list[GraphUIEvent],
    name: str,
    payload: dict[str, Any],
    *,
    persist: bool = True,
) -> None:
    """Store and stream a graph event for downstream translation."""

    event = build_graph_event(name, payload)
    if persist:
        ui_events.append(event)

    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    if writer is not None:
        writer(event)


def convert_tool_event(event: dict[str, Any]) -> GraphUIEvent:
    """Translate legacy tool tracker payloads into internal graph events."""

    payload = dict(event)
    payload.pop("type", None)
    return build_graph_event("tool_call", payload)
