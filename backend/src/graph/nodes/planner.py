"""Planning node for producing structured research tasks."""

from __future__ import annotations

from typing import Callable

try:
    from . import convert_tool_event, emit_graph_event, serialize_task
    from ..state import ResearchGraphState, to_summary_state
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import convert_tool_event, emit_graph_event, serialize_task
    from graph.state import ResearchGraphState, to_summary_state


def make_plan_tasks_node(runtime: object) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the task-planning node."""

    def plan_tasks(state: ResearchGraphState) -> ResearchGraphState:
        ui_events = list(state.get("ui_events", []))
        emit_graph_event(
            ui_events,
            "status",
            {"message": "规划研究任务"},
        )

        legacy_state = to_summary_state(state)
        todo_items = runtime.planner.plan_todo_list(legacy_state)
        if not todo_items:
            todo_items = [runtime.planner.create_fallback_task(legacy_state)]
            legacy_state.todo_items = todo_items

        tool_events = runtime.tool_tracker.drain(legacy_state)
        for tool_event in tool_events:
            internal_event = convert_tool_event(tool_event)
            emit_graph_event(
                ui_events,
                internal_event["name"],
                internal_event["payload"],
                persist=False,
            )

        emit_graph_event(
            ui_events,
            "todo_list",
            {"tasks": [serialize_task(task) for task in todo_items], "step": 0},
        )

        return {
            "status": "running",
            "current_stage": "plan_tasks",
            "todo_items": todo_items,
            "ui_events": ui_events,
        }

    return plan_tasks
