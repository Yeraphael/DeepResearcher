"""Research execution node for explicit search and summarization."""

from __future__ import annotations

from typing import Callable

try:
    from . import convert_tool_event, emit_graph_event
    from .summarize import build_running_summary, stream_task_summary
    from ..state import (
        ResearchGraphState,
        WebResearchResultRecord,
        compact_text,
        to_summary_state,
    )
    from ...services.search import dispatch_search, prepare_research_context
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import convert_tool_event, emit_graph_event
    from graph.nodes.summarize import build_running_summary, stream_task_summary
    from graph.state import ResearchGraphState, WebResearchResultRecord, compact_text, to_summary_state
    from services.search import dispatch_search, prepare_research_context


def make_run_research_tasks_node(runtime: object) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the sequential MVP task-execution node."""

    def run_research_tasks(state: ResearchGraphState) -> ResearchGraphState:
        ui_events = list(state.get("ui_events", []))
        todo_items = list(state.get("todo_items", []))
        sources_gathered = list(state.get("sources_gathered", []))
        web_research_results = list(state.get("web_research_results", []))
        errors = list(state.get("errors", []))

        emit_graph_event(
            ui_events,
            "status",
            {"message": "执行研究任务"},
        )

        for task in todo_items:
            try:
                task.status = "in_progress"
                emit_graph_event(
                    ui_events,
                    "task_status",
                    {
                        "task_id": task.id,
                        "status": task.status,
                        "title": task.title,
                        "intent": task.intent,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                )

                search_result, notices, answer_text, backend = dispatch_search(
                    task.query,
                    runtime.config,
                    len(web_research_results),
                )

                for tool_event in runtime.tool_tracker.drain(
                    to_summary_state({**state, "todo_items": todo_items})
                ):
                    internal_event = convert_tool_event(tool_event)
                    emit_graph_event(
                        ui_events,
                        internal_event["name"],
                        internal_event["payload"],
                        persist=False,
                    )

                if notices:
                    task.notices.extend([notice for notice in notices if notice])
                    for notice in notices:
                        if notice:
                            emit_graph_event(
                                ui_events,
                                "status",
                                {
                                    "message": notice,
                                    "task_id": task.id,
                                },
                            )

                results = (search_result or {}).get("results") or []
                if not results:
                    task.status = "skipped"
                    emit_graph_event(
                        ui_events,
                        "task_status",
                        {
                            "task_id": task.id,
                            "status": task.status,
                            "title": task.title,
                            "intent": task.intent,
                            "note_id": task.note_id,
                            "note_path": task.note_path,
                        },
                    )
                    continue

                sources_summary, context = prepare_research_context(
                    search_result,
                    answer_text,
                    runtime.config,
                )
                task.sources_summary = sources_summary
                sources_gathered.append(sources_summary)
                web_research_results.append(
                    WebResearchResultRecord(
                        task_id=task.id,
                        title=task.title,
                        query=task.query,
                        backend=backend,
                        source_count=len(results),
                        sources_summary=compact_text(sources_summary, max_chars=1500),
                        answer_excerpt=compact_text(answer_text, max_chars=800) if answer_text else None,
                        context_preview=compact_text(context, max_chars=3000),
                    )
                )

                emit_graph_event(
                    ui_events,
                    "sources",
                    {
                        "task_id": task.id,
                        "latest_sources": sources_summary,
                        "sources_summary": sources_summary,
                        "backend": backend,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                    persist=False,
                )

                summary_text = stream_task_summary(
                    runtime=runtime,
                    state={
                        **state,
                        "todo_items": todo_items,
                        "sources_gathered": sources_gathered,
                        "web_research_results": web_research_results,
                    },
                    task=task,
                    context=context,
                    ui_events=ui_events,
                )
                task.summary = summary_text.strip() or "暂无可用信息"
                task.status = "completed"

                emit_graph_event(
                    ui_events,
                    "task_status",
                    {
                        "task_id": task.id,
                        "status": task.status,
                        "title": task.title,
                        "intent": task.intent,
                        "summary": task.summary,
                        "sources_summary": task.sources_summary,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                )
            except Exception as exc:
                task.status = "failed"
                task.summary = str(exc)
                errors.append(f"task_{task.id}: {exc}")
                emit_graph_event(
                    ui_events,
                    "task_status",
                    {
                        "task_id": task.id,
                        "status": task.status,
                        "title": task.title,
                        "intent": task.intent,
                        "detail": str(exc),
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                )

        return {
            "status": "running",
            "current_stage": "run_research_tasks",
            "todo_items": todo_items,
            "sources_gathered": sources_gathered,
            "web_research_results": web_research_results,
            "running_summary": build_running_summary(todo_items),
            "ui_events": ui_events,
            "errors": errors,
        }

    return run_research_tasks
