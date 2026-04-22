"""LangGraph builder for the DeepResearcher MVP workflow."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

try:
    from .nodes.ingest import make_ingest_request_node
    from .nodes.persist import make_persist_report_node
    from .nodes.planner import make_plan_tasks_node
    from .nodes.report import (
        make_build_report_outline_node,
        make_compile_report_node,
    )
    from .nodes.search import (
        make_aggregate_results_node,
        make_dispatch_research_tasks_node,
        make_research_worker_node,
        route_research_workers,
    )
    from .state import ResearchGraphState
except ImportError:  # pragma: no cover - script-mode fallback
    from graph.nodes.ingest import make_ingest_request_node
    from graph.nodes.persist import make_persist_report_node
    from graph.nodes.planner import make_plan_tasks_node
    from graph.nodes.report import make_build_report_outline_node, make_compile_report_node
    from graph.nodes.search import (
        make_aggregate_results_node,
        make_dispatch_research_tasks_node,
        make_research_worker_node,
        route_research_workers,
    )
    from graph.state import ResearchGraphState


def build_research_graph(runtime: Any, checkpointer: Any):
    """Compile the single-graph MVP workflow with SQLite checkpointing."""

    builder = StateGraph(ResearchGraphState)
    builder.add_node("ingest_request", make_ingest_request_node(runtime))
    builder.add_node("plan_tasks", make_plan_tasks_node(runtime))
    builder.add_node("dispatch_research_tasks", make_dispatch_research_tasks_node(runtime))
    builder.add_node("research_worker", make_research_worker_node(runtime))
    builder.add_node("aggregate_results", make_aggregate_results_node(runtime))
    builder.add_node("build_report_outline", make_build_report_outline_node(runtime))
    builder.add_node("compile_report", make_compile_report_node(runtime))
    builder.add_node("persist_report", make_persist_report_node(runtime))

    builder.add_edge(START, "ingest_request")
    builder.add_edge("ingest_request", "plan_tasks")
    builder.add_edge("plan_tasks", "dispatch_research_tasks")
    builder.add_conditional_edges("dispatch_research_tasks", route_research_workers)
    builder.add_edge("research_worker", "aggregate_results")
    builder.add_edge("aggregate_results", "build_report_outline")
    builder.add_edge("build_report_outline", "compile_report")
    builder.add_edge("compile_report", "persist_report")
    builder.add_edge("persist_report", END)

    return builder.compile(checkpointer=checkpointer, name="deep_research_orchestrator_worker")
