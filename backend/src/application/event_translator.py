"""Translate internal LangGraph events into the legacy SSE contract."""

from __future__ import annotations

from typing import Any


LEGACY_EVENT_TYPES = {
    "status",
    "todo_list",
    "task_status",
    "sources",
    "task_summary_chunk",
    "tool_call",
    "final_report",
    "done",
    "error",
}


class GraphEventTranslator:
    """Convert graph stream payloads into the frontend's existing SSE format."""

    def translate_stream_part(self, part: Any) -> list[dict[str, Any]]:
        """Translate a LangGraph stream part into zero or more SSE events."""

        if not isinstance(part, dict):
            return []

        part_type = part.get("type")
        if part_type == "custom":
            return self.translate_event(part.get("data"))

        return []

    def translate_event(self, event: Any) -> list[dict[str, Any]]:
        """Translate one internal graph event payload into legacy SSE events."""

        if not isinstance(event, dict):
            return []

        if event.get("type") in LEGACY_EVENT_TYPES:
            return [event]

        event_name = event.get("name") or event.get("event")
        payload = dict(event.get("payload") or {})

        if not isinstance(event_name, str):
            return []
        if event_name not in LEGACY_EVENT_TYPES:
            return []

        return [{"type": event_name, **payload}]
