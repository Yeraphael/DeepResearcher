"""Report-compilation node for the LangGraph research workflow."""

from __future__ import annotations

from typing import Callable

try:
    from . import convert_tool_event, emit_graph_event
    from ..state import ResearchGraphState, to_summary_state
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import convert_tool_event, emit_graph_event
    from graph.state import ResearchGraphState, to_summary_state


def make_compile_report_node(runtime: object) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the report-compilation node."""

    def compile_report(state: ResearchGraphState) -> ResearchGraphState:
        ui_events = list(state.get("ui_events", []))
        emit_graph_event(
            ui_events,
            "status",
            {"message": "生成最终报告"},
        )

        legacy_state = to_summary_state(state)
        report = runtime.reporting.generate_report(legacy_state)

        for tool_event in runtime.tool_tracker.drain(legacy_state):
            internal_event = convert_tool_event(tool_event)
            emit_graph_event(
                ui_events,
                internal_event["name"],
                internal_event["payload"],
                persist=False,
            )

        return {
            "status": "running",
            "current_stage": "compile_report",
            "structured_report": report,
            "running_summary": report,
            "ui_events": ui_events,
        }

    return compile_report
