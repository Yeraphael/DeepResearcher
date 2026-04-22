"""Application service that runs the LangGraph-backed research workflow."""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator
from uuid import uuid4

from hello_agents import HelloAgentsLLM, ToolAwareSimpleAgent
from hello_agents.tools import ToolRegistry
from hello_agents.tools.builtin.note_tool import NoteTool

try:
    from ..config import Configuration
    from ..graph.builder import build_research_graph
    from ..graph.state import ResearchGraphState, build_initial_graph_state
    from ..infrastructure.checkpoint import (
        SQLiteCheckpointerHandle,
        create_sqlite_checkpointer,
        create_sqlite_checkpointer_async,
    )
    from ..models import SummaryStateOutput
    from ..prompts import (
        report_writer_instructions_v2,
        task_summarizer_instructions,
        todo_planner_system_prompt,
    )
    from ..services.planner import PlanningService
    from ..services.reporter import ReportingService
    from ..services.summarizer import SummarizationService
    from ..services.tool_events import ToolCallTracker
    from .event_translator import GraphEventTranslator
except ImportError:  # pragma: no cover - script-mode fallback
    from application.event_translator import GraphEventTranslator
    from config import Configuration
    from graph.builder import build_research_graph
    from graph.state import ResearchGraphState, build_initial_graph_state
    from infrastructure.checkpoint import (
        SQLiteCheckpointerHandle,
        create_sqlite_checkpointer,
        create_sqlite_checkpointer_async,
    )
    from models import SummaryStateOutput
    from prompts import (
        report_writer_instructions_v2,
        task_summarizer_instructions,
        todo_planner_system_prompt,
    )
    from services.planner import PlanningService
    from services.reporter import ReportingService
    from services.summarizer import SummarizationService
    from services.tool_events import ToolCallTracker


@dataclass(slots=True)
class GraphRuntime:
    """Container for reusable services used by graph nodes."""

    config: Configuration
    planner: Any
    summarizer: Any
    reporting: Any
    tool_tracker: ToolCallTracker
    note_tool: NoteTool | None = None


class ResearchRunner:
    """Run research requests on top of the LangGraph orchestrator-worker workflow."""

    def __init__(
        self,
        config: Configuration | None = None,
        *,
        runtime: GraphRuntime | None = None,
        graph: Any | None = None,
        checkpointer_handle: SQLiteCheckpointerHandle | None = None,
        event_translator: GraphEventTranslator | None = None,
    ) -> None:
        self.config = config or Configuration.from_env()
        self.runtime = runtime or self._build_runtime()
        self._checkpointer_handle = checkpointer_handle or create_sqlite_checkpointer(self.config)
        self.graph = graph or build_research_graph(self.runtime, self._checkpointer_handle.saver)
        self.event_translator = event_translator or GraphEventTranslator()

    @classmethod
    async def create(
        cls,
        config: Configuration | None = None,
        *,
        runtime: GraphRuntime | None = None,
        graph: Any | None = None,
        checkpointer_handle: SQLiteCheckpointerHandle | None = None,
        event_translator: GraphEventTranslator | None = None,
    ) -> "ResearchRunner":
        """Async factory that avoids blocking the running event loop."""

        self = cls.__new__(cls)
        self.config = config or Configuration.from_env()
        self.runtime = runtime or self._build_runtime()
        self._checkpointer_handle = checkpointer_handle or await create_sqlite_checkpointer_async(
            self.config
        )
        self.graph = graph or build_research_graph(self.runtime, self._checkpointer_handle.saver)
        self.event_translator = event_translator or GraphEventTranslator()
        return self

    async def ainvoke(
        self,
        topic: str,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> SummaryStateOutput:
        """Execute the graph non-streaming and return the final output."""

        initial_state, runnable_config = self._prepare_run(
            topic,
            thread_id=thread_id,
            session_id=session_id,
        )
        final_state: ResearchGraphState = await self.graph.ainvoke(
            initial_state,
            config=runnable_config,
        )
        todo_items = list(final_state.get("todo_items", []))
        report = (final_state.get("structured_report") or final_state.get("running_summary") or "").strip()

        return SummaryStateOutput(
            running_summary=report,
            report_markdown=report,
            todo_items=todo_items,
        )

    def invoke(
        self,
        topic: str,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> SummaryStateOutput:
        """Synchronous compatibility wrapper around :meth:`ainvoke`."""

        return asyncio.run(
            self.ainvoke(
                topic,
                thread_id=thread_id,
                session_id=session_id,
            )
        )

    async def astream(
        self,
        topic: str,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute the graph in streaming mode using the legacy SSE shape."""

        initial_state, runnable_config = self._prepare_run(
            topic,
            thread_id=thread_id,
            session_id=session_id,
        )

        async for part in self.graph.astream(
            initial_state,
            config=runnable_config,
            stream_mode="custom",
            version="v2",
        ):
            for translated_event in self.event_translator.translate_stream_part(part):
                yield translated_event

        yield {"type": "done"}

    def stream(
        self,
        topic: str,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Synchronous compatibility wrapper around :meth:`astream`."""

        event_queue: queue.Queue[dict[str, Any] | BaseException | object] = queue.Queue()
        sentinel = object()

        def producer() -> None:
            async def run() -> None:
                try:
                    async for event in self.astream(
                        topic,
                        thread_id=thread_id,
                        session_id=session_id,
                    ):
                        event_queue.put(event)
                except BaseException as exc:  # pragma: no cover - sync wrapper fallback
                    event_queue.put(exc)
                finally:
                    event_queue.put(sentinel)

            asyncio.run(run())

        worker = threading.Thread(target=producer, name="research-stream", daemon=True)
        worker.start()

        while True:
            item = event_queue.get()
            if item is sentinel:
                break
            if isinstance(item, BaseException):
                raise item
            yield item

        worker.join(timeout=0.1)

    def close(self) -> None:
        """Release request-scoped infrastructure resources."""

        asyncio.run(self.aclose())

    async def aclose(self) -> None:
        """Release request-scoped infrastructure resources asynchronously."""

        await self._checkpointer_handle.aclose()

    def _prepare_run(
        self,
        topic: str,
        *,
        thread_id: str | None,
        session_id: str | None,
    ) -> tuple[ResearchGraphState, dict[str, Any]]:
        resolved_thread_id = thread_id or uuid4().hex
        resolved_session_id = session_id or resolved_thread_id
        run_id = uuid4().hex
        initial_state = build_initial_graph_state(
            topic=topic.strip(),
            session_id=resolved_session_id,
            thread_id=resolved_thread_id,
            run_id=run_id,
        )
        runnable_config: dict[str, Any] = {
            "configurable": {
                "thread_id": resolved_thread_id,
                "session_id": resolved_session_id,
                "run_id": run_id,
            },
            "max_concurrency": self.config.max_parallel_research_tasks,
        }
        return initial_state, runnable_config

    def _build_runtime(self) -> GraphRuntime:
        """Create the default runtime services and shared tools."""

        llm = self._init_llm()
        note_tool = NoteTool(workspace=self.config.notes_workspace) if self.config.enable_notes else None
        tools_registry: ToolRegistry | None = None
        if note_tool:
            registry = ToolRegistry()
            registry.register_tool(note_tool)
            tools_registry = registry

        tool_tracker = ToolCallTracker(
            self.config.notes_workspace if self.config.enable_notes else None
        )

        def make_agent(
            *,
            name: str,
            system_prompt: str,
            allow_tools: bool = False,
        ) -> ToolAwareSimpleAgent:
            enable_tools = allow_tools and tools_registry is not None
            return ToolAwareSimpleAgent(
                name=name,
                llm=llm,
                system_prompt=system_prompt,
                enable_tool_calling=enable_tools,
                tool_registry=tools_registry if enable_tools else None,
                tool_call_listener=tool_tracker.record if enable_tools else None,
            )

        todo_agent = make_agent(
            name="研究规划专家",
            system_prompt=todo_planner_system_prompt.strip(),
            allow_tools=note_tool is not None,
        )
        report_agent = make_agent(
            name="报告撰写专家",
            system_prompt=report_writer_instructions_v2.strip(),
            allow_tools=False,
        )

        def summarizer_factory() -> ToolAwareSimpleAgent:
            return make_agent(
                name="任务总结专家",
                system_prompt=task_summarizer_instructions.strip(),
                allow_tools=False,
            )

        planner = PlanningService(todo_agent, self.config)
        summarizer = SummarizationService(
            summarizer_factory,
            self.config,
            include_note_guidance=False,
        )
        reporting = ReportingService(report_agent, self.config)
        return GraphRuntime(
            config=self.config,
            planner=planner,
            summarizer=summarizer,
            reporting=reporting,
            tool_tracker=tool_tracker,
            note_tool=note_tool,
        )

    def _init_llm(self) -> HelloAgentsLLM:
        """Instantiate the configured LLM client."""

        llm_kwargs: dict[str, Any] = {"temperature": 0.0}
        model_id = self.config.llm_model_id or self.config.local_llm
        if model_id:
            llm_kwargs["model"] = model_id

        provider = (self.config.llm_provider or "").strip()
        if provider:
            llm_kwargs["provider"] = provider

        if provider == "ollama":
            llm_kwargs["base_url"] = self.config.sanitized_ollama_url()
            llm_kwargs["api_key"] = self.config.llm_api_key or "ollama"
        elif provider == "lmstudio":
            llm_kwargs["base_url"] = self.config.lmstudio_base_url
            if self.config.llm_api_key:
                llm_kwargs["api_key"] = self.config.llm_api_key
        else:
            if self.config.llm_base_url:
                llm_kwargs["base_url"] = self.config.llm_base_url
            if self.config.llm_api_key:
                llm_kwargs["api_key"] = self.config.llm_api_key

        return HelloAgentsLLM(**llm_kwargs)
