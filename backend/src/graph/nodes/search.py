"""Orchestrator-worker nodes for explicit search and summarization."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from langgraph.types import Send

try:
    from . import emit_graph_event
    from .summarize import build_running_summary
    from ..state import (
        ResearchGraphState,
        TaskExecutionResult,
        WebResearchResultRecord,
        compact_text,
        extract_citations,
        extract_key_findings,
        merge_task_results,
        task_spec_to_todo_item,
        to_summary_state,
    )
    from ...services.search import dispatch_search, prepare_research_context
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes import emit_graph_event
    from graph.nodes.summarize import build_running_summary
    from graph.state import (
        ResearchGraphState,
        TaskExecutionResult,
        WebResearchResultRecord,
        compact_text,
        extract_citations,
        extract_key_findings,
        merge_task_results,
        task_spec_to_todo_item,
        to_summary_state,
    )
    from services.search import dispatch_search, prepare_research_context


def make_dispatch_research_tasks_node(runtime: object) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the orchestrator node that prepares worker fan-out."""

    def dispatch_research_tasks(state: ResearchGraphState) -> ResearchGraphState:
        ui_events = list(state.get("ui_events", []))
        emit_graph_event(
            ui_events,
            "status",
            {"message": "并行执行研究任务"},
        )

        return {
            "status": "running",
            "current_stage": "dispatch_research_tasks",
            "ui_events": ui_events,
        }

    return dispatch_research_tasks


def route_research_workers(state: ResearchGraphState) -> list[Send] | list[str]:
    """Send each planned task to an async worker at runtime."""

    task_specs = list(state.get("task_specs", []))
    if not task_specs:
        return ["aggregate_results"]

    return [
        Send(
            "research_worker",
            {
                "session_id": state.get("session_id"),
                "thread_id": state.get("thread_id"),
                "run_id": state.get("run_id"),
                "topic": state.get("topic"),
                "active_task": task_spec,
                "active_task_index": index,
            },
        )
        for index, task_spec in enumerate(task_specs)
    ]


def make_research_worker_node(
    runtime: object,
) -> Callable[[ResearchGraphState], Any]:
    """Create the async worker node that runs one search + summarization task."""

    async def research_worker(state: ResearchGraphState) -> dict[str, list[TaskExecutionResult]]:
        task_spec = state.get("active_task")
        if not task_spec:
            return {"task_results": []}

        task = task_spec_to_todo_item(task_spec)
        loop_count = int(state.get("active_task_index") or 0)
        transient_events: list[dict[str, Any]] = []

        emit_graph_event(
            transient_events,
            "task_status",
            {
                "task_id": task.id,
                "status": "in_progress",
                "title": task.title,
                "intent": task.intent,
                "dimension": task.dimension,
                "note_id": task.note_id,
                "note_path": task.note_path,
            },
            persist=False,
        )

        try:
            search_result, notices, answer_text, backend = await asyncio.to_thread(
                dispatch_search,
                task.query,
                runtime.config,
                loop_count,
            )
            task.notices.extend([notice for notice in notices if notice])

            for notice in notices:
                if notice:
                    emit_graph_event(
                        transient_events,
                        "status",
                        {
                            "message": notice,
                            "task_id": task.id,
                        },
                        persist=False,
                    )

            results = (search_result or {}).get("results") or []
            if not results:
                task.status = "skipped"
                task.summary = "暂无可用信息"
                emit_graph_event(
                    transient_events,
                    "task_status",
                    {
                        "task_id": task.id,
                        "status": task.status,
                        "title": task.title,
                        "intent": task.intent,
                        "dimension": task.dimension,
                        "summary": task.summary,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                    persist=False,
                )
                return {
                    "task_results": [
                        _build_task_result(
                            task=task,
                            backend=backend,
                            source_count=0,
                            answer_text=answer_text,
                            context="",
                            error=None,
                        )
                    ]
                }

            sources_summary, context = prepare_research_context(
                search_result,
                answer_text,
                runtime.config,
            )
            task.sources_summary = sources_summary

            emit_graph_event(
                transient_events,
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

            summary_text = await _stream_task_summary_async(
                runtime=runtime,
                state=state,
                task=task,
                context=context,
            )
            task.summary = summary_text.strip() or "暂无可用信息"
            task.status = "completed"

            emit_graph_event(
                transient_events,
                "task_status",
                {
                    "task_id": task.id,
                    "status": task.status,
                    "title": task.title,
                    "intent": task.intent,
                    "dimension": task.dimension,
                    "summary": task.summary,
                    "sources_summary": task.sources_summary,
                    "note_id": task.note_id,
                    "note_path": task.note_path,
                },
                persist=False,
            )

            return {
                "task_results": [
                    _build_task_result(
                        task=task,
                        backend=backend,
                        source_count=len(results),
                        answer_text=answer_text,
                        context=context,
                        error=None,
                    )
                ]
            }
        except Exception as exc:
            task.status = "failed"
            task.summary = str(exc)
            emit_graph_event(
                transient_events,
                "task_status",
                {
                    "task_id": task.id,
                    "status": task.status,
                    "title": task.title,
                    "intent": task.intent,
                    "dimension": task.dimension,
                    "detail": str(exc),
                    "note_id": task.note_id,
                    "note_path": task.note_path,
                },
                persist=False,
            )
            return {
                "task_results": [
                    _build_task_result(
                        task=task,
                        backend="error",
                        source_count=0,
                        answer_text=None,
                        context="",
                        error=str(exc),
                    )
                ]
            }

    return research_worker


def make_aggregate_results_node(runtime: object) -> Callable[[ResearchGraphState], ResearchGraphState]:
    """Create the reducer node that merges worker outputs into report inputs."""

    def aggregate_results(state: ResearchGraphState) -> ResearchGraphState:
        ui_events = list(state.get("ui_events", []))
        emit_graph_event(
            ui_events,
            "status",
            {"message": "汇总研究结果"},
        )

        ordered_task_results = sorted(
            state.get("task_results", []),
            key=lambda item: int(item.get("task_id") or 0),
        )
        todo_items = merge_task_results(list(state.get("todo_items", [])), ordered_task_results)
        web_research_results = [
            WebResearchResultRecord(
                task_id=int(result["task_id"]),
                title=result.get("title") or f"任务 {result['task_id']}",
                query=result.get("query") or "",
                backend=result.get("backend") or "unknown",
                source_count=int(result.get("source_count") or 0),
                sources_summary=compact_text(result.get("sources_summary"), max_chars=1500),
                answer_excerpt=compact_text(result.get("answer_excerpt"), max_chars=800)
                if result.get("answer_excerpt")
                else None,
                context_preview=compact_text(result.get("context_preview"), max_chars=3000),
            )
            for result in ordered_task_results
            if result.get("sources_summary") or result.get("context_preview") or result.get("source_count")
        ]
        sources_gathered = [
            task.sources_summary
            for task in todo_items
            if task.sources_summary
        ]
        errors = [
            f"task_{result['task_id']}: {result['error']}"
            for result in ordered_task_results
            if result.get("error")
        ]

        return {
            "status": "running",
            "current_stage": "aggregate_results",
            "todo_items": todo_items,
            "sources_gathered": sources_gathered,
            "web_research_results": web_research_results,
            "running_summary": build_running_summary(todo_items),
            "errors": errors,
            "ui_events": ui_events,
        }

    return aggregate_results


async def _stream_task_summary_async(
    *,
    runtime: object,
    state: ResearchGraphState,
    task: Any,
    context: str,
) -> str:
    """Bridge the sync task-summary stream into an async LangGraph worker."""

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, str | Exception]] = asyncio.Queue()
    summary_state = {
        **state,
        "todo_items": [task],
        "sources_gathered": [task.sources_summary] if task.sources_summary else [],
        "web_research_results": [],
    }
    legacy_state = to_summary_state(summary_state)

    def producer() -> None:
        try:
            summary_stream, summary_getter = runtime.summarizer.stream_task_summary(
                legacy_state,
                task,
                context,
            )
            for chunk in summary_stream:
                asyncio.run_coroutine_threadsafe(queue.put(("chunk", chunk)), loop).result()
            final_summary = summary_getter().strip()
            asyncio.run_coroutine_threadsafe(queue.put(("done", final_summary)), loop).result()
        except Exception as exc:  # pragma: no cover - defensive
            asyncio.run_coroutine_threadsafe(queue.put(("error", exc)), loop).result()

    worker = threading.Thread(
        target=producer,
        name=f"task-summary-{task.id}",
        daemon=True,
    )
    worker.start()

    final_summary = ""
    while True:
        kind, payload = await queue.get()
        if kind == "chunk":
            chunk = str(payload)
            if chunk:
                emit_graph_event(
                    [],
                    "task_summary_chunk",
                    {
                        "task_id": task.id,
                        "content": chunk,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                    persist=False,
                )
        elif kind == "done":
            final_summary = str(payload)
            break
        elif kind == "error":
            if isinstance(payload, Exception):
                raise payload
            raise RuntimeError(str(payload))

    worker.join(timeout=0.1)
    return final_summary or "暂无可用信息"


def _build_task_result(
    *,
    task: Any,
    backend: str,
    source_count: int,
    answer_text: str | None,
    context: str,
    error: str | None,
) -> TaskExecutionResult:
    """Build the compact worker result stored in graph state."""

    citations = extract_citations(task.sources_summary or "")
    key_findings = extract_key_findings(task.summary or "")
    evidence_points = key_findings or extract_key_findings(task.sources_summary or "")

    return TaskExecutionResult(
        task_id=task.id,
        title=task.title,
        intent=task.intent,
        query=task.query,
        dimension=task.dimension,
        status=task.status,
        summary=task.summary or "",
        sources_summary=task.sources_summary or "",
        notices=list(task.notices or []),
        note_id=task.note_id,
        note_path=task.note_path,
        backend=backend,
        source_count=source_count,
        answer_excerpt=compact_text(answer_text, max_chars=800) if answer_text else None,
        context_preview=compact_text(context, max_chars=3000),
        key_findings=key_findings,
        evidence_points=evidence_points,
        citations=citations,
        error=error,
    )
