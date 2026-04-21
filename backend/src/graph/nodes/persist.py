"""Persistence node for explicit report saving."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

try:
    from . import emit_graph_event
    from ..state import ResearchGraphState
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import emit_graph_event
    from graph.state import ResearchGraphState


NOTE_ID_PATTERN = re.compile(r"ID:\s*([^\n]+)")


def make_persist_report_node(runtime: object) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the explicit report-persistence node."""

    def persist_report(state: ResearchGraphState) -> ResearchGraphState:
        ui_events = list(state.get("ui_events", []))
        report = (state.get("structured_report") or state.get("running_summary") or "").strip()
        report_note_id = state.get("report_note_id")
        report_note_path = state.get("report_note_path")

        emit_graph_event(
            ui_events,
            "status",
            {"message": "持久化最终报告"},
        )

        if report:
            report_note_id, report_note_path = _persist_report(runtime, state, report)

        emit_graph_event(
            ui_events,
            "final_report",
            {
                "report": report,
                "note_id": report_note_id,
                "note_path": report_note_path,
            },
        )

        return {
            "status": "completed",
            "current_stage": "persist_report",
            "report_note_id": report_note_id,
            "report_note_path": report_note_path,
            "ui_events": ui_events,
        }

    return persist_report


def _persist_report(runtime: object, state: ResearchGraphState, report: str) -> tuple[str | None, str | None]:
    """Persist the final report using NoteTool or a markdown-file fallback."""

    topic = (state.get("topic") or "研究报告").strip()
    title = f"研究报告：{topic}".strip()

    if runtime.note_tool is not None:
        note_id = state.get("report_note_id")
        response = ""

        if note_id:
            response = runtime.note_tool.run(
                {
                    "action": "update",
                    "note_id": note_id,
                    "title": title,
                    "note_type": "conclusion",
                    "tags": ["deep_research", "report"],
                    "content": report,
                }
            )
            if isinstance(response, str) and response.startswith("❌"):
                note_id = None

        if not note_id:
            response = runtime.note_tool.run(
                {
                    "action": "create",
                    "title": title,
                    "note_type": "conclusion",
                    "tags": ["deep_research", "report"],
                    "content": report,
                }
            )
            note_id = _extract_note_id(response)

        if note_id:
            note_path = Path(runtime.config.notes_workspace) / f"{note_id}.md"
            return note_id, str(note_path)

    fallback_dir = Path(runtime.config.notes_workspace)
    fallback_dir.mkdir(parents=True, exist_ok=True)
    fallback_name = f"report_{state.get('run_id') or 'latest'}.md"
    fallback_path = fallback_dir / fallback_name
    fallback_path.write_text(report, encoding="utf-8")
    return fallback_path.stem, str(fallback_path)


def _extract_note_id(response: str | None) -> str | None:
    """Extract a note id from a NoteTool response payload."""

    if not response:
        return None

    match = NOTE_ID_PATTERN.search(response)
    if not match:
        return None
    return match.group(1).strip()
