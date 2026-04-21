"""SQLite checkpointer wiring for LangGraph."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

try:
    from ..config import Configuration
except ImportError:  # pragma: no cover - script-mode fallback
    from config import Configuration


@dataclass(slots=True)
class SQLiteCheckpointerHandle:
    """Owns the SQLite connection and the LangGraph saver wrapper."""

    saver: SqliteSaver
    connection: sqlite3.Connection
    path: Path

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self.connection.close()


def create_sqlite_checkpointer(config: Configuration) -> SQLiteCheckpointerHandle:
    """Create the default SQLite checkpointer for the research graph."""

    checkpoint_path = config.resolved_checkpoint_path()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    saver = SqliteSaver(connection)
    return SQLiteCheckpointerHandle(
        saver=saver,
        connection=connection,
        path=checkpoint_path,
    )
