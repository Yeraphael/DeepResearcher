"""SQLite checkpointer wiring for LangGraph."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

try:
    from ..config import Configuration
except ImportError:  # pragma: no cover - script-mode fallback
    from config import Configuration


@dataclass(slots=True)
class SQLiteCheckpointerHandle:
    """Owns the SQLite connection and the LangGraph saver wrapper."""

    saver: Any
    connection: aiosqlite.Connection
    path: Path

    async def aclose(self) -> None:
        """Close the underlying async SQLite connection."""

        await self.connection.close()

    def close(self) -> None:
        """Synchronous compatibility wrapper around :meth:`aclose`."""

        asyncio.run(self.aclose())


def create_sqlite_checkpointer(config: Configuration) -> SQLiteCheckpointerHandle:
    """Create the default SQLite checkpointer for synchronous contexts."""

    return asyncio.run(create_sqlite_checkpointer_async(config))


async def create_sqlite_checkpointer_async(config: Configuration) -> SQLiteCheckpointerHandle:
    """Create the default SQLite checkpointer for the research graph."""

    checkpoint_path = config.resolved_checkpoint_path()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(checkpoint_path)
    saver = AsyncSqliteSaver(connection)
    return SQLiteCheckpointerHandle(
        saver=saver,
        connection=connection,
        path=checkpoint_path,
    )
