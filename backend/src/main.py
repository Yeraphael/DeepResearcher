"""FastAPI entrypoint exposing the DeepResearchAgent and persisted sessions."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from agent import DeepResearchAgent
from config import Configuration, SearchAPI
from session_store import ResearchSessionStore

logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <4}</level> | <cyan>{file}:{line}</cyan> | <level>{message}</level>",
    colorize=True,
)


class ResearchRequest(BaseModel):
    """Legacy payload for triggering a one-off research run."""

    topic: str = Field(..., description="Research topic supplied by the user")
    search_api: SearchAPI | None = Field(
        default=None,
        description="Override the default search backend configured via env",
    )


class ResearchResponse(BaseModel):
    """Legacy response for synchronous research runs."""

    report_markdown: str = Field(
        ..., description="Markdown-formatted research report including sections"
    )
    todo_items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Structured TODO items with summaries and sources",
    )


class ResearchSessionCreateRequest(BaseModel):
    """Request payload for creating a new persisted research session."""

    topic: str | None = Field(default=None, description="Optional initial draft topic")
    search_api: SearchAPI | None = Field(
        default=None,
        description="Optional initial draft search backend",
    )


class ResearchSessionRunRequest(BaseModel):
    """Request payload for executing a persisted research session."""

    topic: str = Field(..., description="Research topic to run inside the session")
    search_api: SearchAPI | None = Field(
        default=None,
        description="Optional search backend override for this session",
    )


def _mask_secret(value: str | None, visible: int = 4) -> str:
    if not value:
        return "unset"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def _config_base_url(config: Configuration) -> str:
    if config.llm_provider == "ollama":
        return config.sanitized_ollama_url()
    if config.llm_provider == "lmstudio":
        return config.lmstudio_base_url
    return config.llm_base_url or "unset"


def _build_config(search_api: SearchAPI | None = None) -> Configuration:
    overrides: dict[str, Any] = {}
    if search_api is not None:
        overrides["search_api"] = search_api
    return Configuration.from_env(overrides=overrides, validate_runtime=True)


def _humanize_runtime_error(exc: Exception) -> str:
    detail = str(exc).strip() or "研究过程中发生未知错误。"
    lower_detail = detail.lower()

    if "invalid_api_key" in lower_detail or "incorrect api key" in lower_detail:
        return (
            "LLM 鉴权失败：当前 LLM_API_KEY 无效或已过期。"
            "请检查 backend/.env 中的 LLM_API_KEY、LLM_BASE_URL 和账号侧模型权限。"
        )

    if "model '" in lower_detail and "not found" in lower_detail:
        return (
            "LLM 模型不存在：当前 LLM_MODEL_ID 在目标服务中不可用。"
            "请检查 backend/.env 中的 LLM_MODEL_ID 是否与平台实际模型名一致。"
        )

    if "connection error" in lower_detail:
        return "LLM 连接失败，请检查网络、代理设置以及 LLM_BASE_URL 是否可访问。"

    if "authenticationerror" in lower_detail:
        return "LLM 鉴权失败，请检查模型服务的 API Key 和 Base URL 配置。"

    return detail


def _serialize_legacy_result(result: Any) -> ResearchResponse:
    todo_payload = [
        {
            "id": item.id,
            "title": item.title,
            "intent": item.intent,
            "query": item.query,
            "status": item.status,
            "summary": item.summary,
            "sources_summary": item.sources_summary,
            "note_id": item.note_id,
            "note_path": item.note_path,
        }
        for item in result.todo_items
    ]
    return ResearchResponse(
        report_markdown=(result.report_markdown or result.running_summary or ""),
        todo_items=todo_payload,
    )


def _persisted_stream(
    *,
    store: ResearchSessionStore,
    session_id: int,
    agent: DeepResearchAgent,
    topic: str,
) -> Iterator[dict[str, Any]]:
    try:
        for event in agent.run_stream(topic):
            store.record_event(session_id, event)
            yield event
    except Exception as exc:  # pragma: no cover - defensive guardrail
        logger.exception("Streaming research failed")
        error_payload = {
            "type": "error",
            "detail": _humanize_runtime_error(exc),
        }
        store.record_event(session_id, error_payload)
        yield error_payload


def _prepare_session_agent(
    *,
    store: ResearchSessionStore,
    session_id: int,
    payload: ResearchSessionRunRequest,
) -> DeepResearchAgent:
    prepared = False
    try:
        config = _build_config(payload.search_api)
        store.prepare_session_run(
            session_id,
            topic=payload.topic,
            search_api=payload.search_api.value if payload.search_api else None,
        )
        prepared = True
        return DeepResearchAgent(config=config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究 session 不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guardrail
        if prepared:
            store.record_event(
                session_id,
                {
                    "type": "error",
                    "detail": _humanize_runtime_error(exc),
                },
            )
        raise HTTPException(
            status_code=500,
            detail=_humanize_runtime_error(exc),
        ) from exc


def create_app() -> FastAPI:
    base_config = Configuration.from_env(validate_runtime=False)
    store = ResearchSessionStore(base_config.database_path)

    app = FastAPI(title="DeepResearcher API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def log_startup_configuration() -> None:
        logger.info(
            "DeepResearch configuration loaded: provider=%s model=%s base_url=%s search_api=%s "
            "max_loops=%s fetch_full_page=%s tool_calling=%s strip_thinking=%s api_key=%s db=%s",
            base_config.llm_provider,
            base_config.resolved_model() or "unset",
            _config_base_url(base_config),
            (
                base_config.search_api.value
                if isinstance(base_config.search_api, SearchAPI)
                else base_config.search_api
            ),
            base_config.max_web_research_loops,
            base_config.fetch_full_page,
            base_config.use_tool_calling,
            base_config.strip_thinking_tokens,
            _mask_secret(base_config.llm_api_key),
            store.database_path,
        )

        try:
            base_config.validate_runtime()
        except ValueError as exc:
            logger.warning("Runtime LLM config is currently incomplete: %s", exc)

    @app.get("/healthz")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/research/sessions")
    def list_research_sessions() -> list[dict[str, Any]]:
        return store.list_sessions()

    @app.get("/api/research/sessions/{session_id}")
    def get_research_session(session_id: int) -> dict[str, Any]:
        detail = store.get_session(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="研究 session 不存在。")
        return detail

    @app.post(
        "/api/research/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    def create_research_session(
        payload: ResearchSessionCreateRequest | None = None,
    ) -> dict[str, Any]:
        request_payload = payload or ResearchSessionCreateRequest()
        return store.create_session(
            topic=request_payload.topic,
            search_api=(
                request_payload.search_api.value
                if request_payload.search_api is not None
                else None
            ),
        )

    @app.post("/api/research/sessions/{session_id}/run")
    def run_research_session(
        session_id: int,
        payload: ResearchSessionRunRequest,
    ) -> dict[str, Any]:
        agent = _prepare_session_agent(
            store=store,
            session_id=session_id,
            payload=payload,
        )

        for _event in _persisted_stream(
            store=store,
            session_id=session_id,
            agent=agent,
            topic=payload.topic,
        ):
            pass

        detail = store.get_session(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="研究 session 不存在。")
        return detail

    @app.post("/api/research/sessions/{session_id}/run/stream")
    def run_research_session_stream(
        session_id: int,
        payload: ResearchSessionRunRequest,
    ) -> StreamingResponse:
        agent = _prepare_session_agent(
            store=store,
            session_id=session_id,
            payload=payload,
        )

        def event_iterator() -> Iterator[str]:
            for event in _persisted_stream(
                store=store,
                session_id=session_id,
                agent=agent,
                topic=payload.topic,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # ------------------------------------------------------------------
    # Legacy routes kept for compatibility
    # ------------------------------------------------------------------
    @app.post("/research", response_model=ResearchResponse)
    def run_research(payload: ResearchRequest) -> ResearchResponse:
        try:
            config = _build_config(payload.search_api)
            agent = DeepResearchAgent(config=config)
            result = agent.run(payload.topic)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive guardrail
            raise HTTPException(
                status_code=500,
                detail=_humanize_runtime_error(exc),
            ) from exc

        return _serialize_legacy_result(result)

    @app.post("/research/stream")
    def stream_research(payload: ResearchRequest) -> StreamingResponse:
        try:
            config = _build_config(payload.search_api)
            agent = DeepResearchAgent(config=config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def event_iterator() -> Iterator[str]:
            try:
                for event in agent.run_stream(payload.topic):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # pragma: no cover - defensive guardrail
                logger.exception("Streaming research failed")
                error_payload = {
                    "type": "error",
                    "detail": _humanize_runtime_error(exc),
                }
                yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.delete("/api/research/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_research_session(_session_id: int) -> Response:
        raise HTTPException(status_code=405, detail="当前版本暂不支持删除历史研究。")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
