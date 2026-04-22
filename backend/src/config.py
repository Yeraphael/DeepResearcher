import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class SearchAPI(Enum):
    PERPLEXITY = "perplexity"
    TAVILY = "tavily"
    DUCKDUCKGO = "duckduckgo"
    SEARXNG = "searxng"
    ADVANCED = "advanced"


class Configuration(BaseModel):
    """Configuration options for the deep research assistant."""

    max_web_research_loops: int = Field(
        default=3,
        title="Research Depth",
        description="Number of research iterations to perform",
    )
    max_parallel_research_tasks: int = Field(
        default=3,
        title="Parallel Research Tasks",
        description="Maximum number of LangGraph research workers to execute concurrently",
    )
    local_llm: str = Field(
        default="llama3.2",
        title="Local Model Name",
        description="Name of the locally hosted LLM (Ollama/LMStudio)",
    )
    llm_provider: str = Field(
        default="ollama",
        title="LLM Provider",
        description="Provider identifier (ollama, lmstudio, or custom)",
    )
    search_api: SearchAPI = Field(
        default=SearchAPI.DUCKDUCKGO,
        title="Search API",
        description="Web search API to use",
    )
    enable_notes: bool = Field(
        default=True,
        title="Enable Notes",
        description="Whether to store task progress in NoteTool",
    )
    notes_workspace: str = Field(
        default="./notes",
        title="Notes Workspace",
        description="Directory for NoteTool to persist task notes",
    )
    fetch_full_page: bool = Field(
        default=True,
        title="Fetch Full Page",
        description="Include the full page content in the search results",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        title="Ollama Base URL",
        description="Base URL for Ollama API (without /v1 suffix)",
    )
    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        title="LMStudio Base URL",
        description="Base URL for LMStudio OpenAI-compatible API",
    )
    strip_thinking_tokens: bool = Field(
        default=True,
        title="Strip Thinking Tokens",
        description="Whether to strip <think> tokens from model responses",
    )
    use_tool_calling: bool = Field(
        default=False,
        title="Use Tool Calling",
        description="Use tool calling instead of JSON mode for structured output",
    )
    llm_api_key: Optional[str] = Field(
        default=None,
        title="LLM API Key",
        description="Optional API key when using custom OpenAI-compatible services",
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        title="LLM Base URL",
        description="Optional base URL when using custom OpenAI-compatible services",
    )
    llm_model_id: Optional[str] = Field(
        default=None,
        title="LLM Model ID",
        description="Optional model identifier for custom OpenAI-compatible services",
    )
    langgraph_checkpoint_path: str = Field(
        default="data/langgraph_checkpoints.sqlite",
        title="LangGraph Checkpoint Path",
        description="SQLite file used to persist LangGraph checkpoints",
    )

    def validate_runtime(self) -> None:
        """Validate the minimum runtime configuration before executing requests."""

        provider = (self.llm_provider or "").strip().lower()
        model_id = (self.llm_model_id or self.local_llm or "").strip()
        base_url = (self.llm_base_url or "").strip()
        api_key = (self.llm_api_key or "").strip()

        if provider == "custom":
            missing = []
            if not model_id:
                missing.append("LLM_MODEL_ID")
            if not base_url:
                missing.append("LLM_BASE_URL")
            if not api_key:
                missing.append("LLM_API_KEY")
            if missing:
                raise ValueError(
                    "自定义模型配置不完整，请检查这些环境变量：" + ", ".join(missing)
                )

        placeholders = {
            "LLM_MODEL_ID": {"your-model-name"},
            "LLM_API_KEY": {"your-api-key-here"},
            "LLM_BASE_URL": {"your-api-base-url"},
        }
        current_values = {
            "LLM_MODEL_ID": model_id,
            "LLM_API_KEY": api_key,
            "LLM_BASE_URL": base_url,
        }

        for env_name, invalid_values in placeholders.items():
            value = current_values[env_name]
            if value in invalid_values:
                raise ValueError(
                    f"{env_name} 仍然是示例占位值，请替换为真实配置后再启动。"
                )

    @classmethod
    def from_env(cls, overrides: Optional[dict[str, Any]] = None) -> "Configuration":
        """Create a configuration object using environment variables and overrides."""

        raw_values: dict[str, Any] = {}

        # Load values from environment variables based on field names
        for field_name in cls.model_fields.keys():
            env_key = field_name.upper()
            if env_key in os.environ:
                raw_values[field_name] = os.environ[env_key]

        # Additional mappings for explicit env names
        env_aliases = {
            "local_llm": os.getenv("LOCAL_LLM"),
            "llm_provider": os.getenv("LLM_PROVIDER"),
            "llm_api_key": os.getenv("LLM_API_KEY"),
            "llm_model_id": os.getenv("LLM_MODEL_ID"),
            "llm_base_url": os.getenv("LLM_BASE_URL"),
            "lmstudio_base_url": os.getenv("LMSTUDIO_BASE_URL"),
            "ollama_base_url": os.getenv("OLLAMA_BASE_URL"),
            "max_web_research_loops": os.getenv("MAX_WEB_RESEARCH_LOOPS"),
            "max_parallel_research_tasks": os.getenv("MAX_PARALLEL_RESEARCH_TASKS"),
            "fetch_full_page": os.getenv("FETCH_FULL_PAGE"),
            "strip_thinking_tokens": os.getenv("STRIP_THINKING_TOKENS"),
            "use_tool_calling": os.getenv("USE_TOOL_CALLING"),
            "search_api": os.getenv("SEARCH_API"),
            "enable_notes": os.getenv("ENABLE_NOTES"),
            "notes_workspace": os.getenv("NOTES_WORKSPACE"),
            "langgraph_checkpoint_path": os.getenv("LANGGRAPH_CHECKPOINT_PATH"),
        }

        for key, value in env_aliases.items():
            if value is not None:
                raw_values.setdefault(key, value)

        if overrides:
            for key, value in overrides.items():
                if value is not None:
                    raw_values[key] = value

        config = cls(**raw_values)
        config.validate_runtime()
        return config

    def sanitized_ollama_url(self) -> str:
        """Ensure Ollama base URL includes the /v1 suffix required by OpenAI clients."""

        base = self.ollama_base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base

    def resolved_model(self) -> Optional[str]:
        """Best-effort resolution of the model identifier to use."""

        return self.llm_model_id or self.local_llm

    def resolved_checkpoint_path(self) -> Path:
        """Return the absolute LangGraph checkpoint database path."""

        path = Path(self.langgraph_checkpoint_path)
        if path.is_absolute():
            return path
        backend_root = Path(__file__).resolve().parent.parent
        return backend_root / path

