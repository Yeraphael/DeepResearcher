"""Compatibility shell delegating research execution to LangGraph."""

from __future__ import annotations

from typing import Iterator

try:
    from .application.research_runner import ResearchRunner
    from .config import Configuration
    from .models import SummaryStateOutput
except ImportError:  # pragma: no cover - script-mode fallback
    from application.research_runner import ResearchRunner
    from config import Configuration
    from models import SummaryStateOutput


class DeepResearchAgent:
    """Backward-compatible agent facade backed by the LangGraph runner."""

    def __init__(
        self,
        config: Configuration | None = None,
        *,
        runner: ResearchRunner | None = None,
    ) -> None:
        self.config = config or Configuration.from_env()
        self._runner = runner or ResearchRunner(config=self.config)

    def run(
        self,
        topic: str,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> SummaryStateOutput:
        """Execute the research workflow and return the final report."""

        return self._runner.invoke(
            topic,
            thread_id=thread_id,
            session_id=session_id,
        )

    def run_stream(
        self,
        topic: str,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> Iterator[dict[str, object]]:
        """Execute the workflow yielding incremental progress events."""

        yield from self._runner.stream(
            topic,
            thread_id=thread_id,
            session_id=session_id,
        )

    def close(self) -> None:
        """Release request-scoped resources."""

        self._runner.close()

    @property
    def _tool_call_events(self) -> list[dict[str, object]]:
        """Expose recorded tool events for legacy integrations."""

        return self._runner.runtime.tool_tracker.as_dicts()


def run_deep_research(topic: str, config: Configuration | None = None) -> SummaryStateOutput:
    """Convenience function mirroring the class-based API."""

    agent = DeepResearchAgent(config=config)
    try:
        return agent.run(topic)
    finally:
        agent.close()
