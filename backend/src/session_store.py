"""SQLite-backed persistence for research sessions and their artifacts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    return row.get(key, default)


class ResearchSessionStore:
    """Handles persistence and reconstruction of research sessions."""

    def __init__(self, database_path: str) -> None:
        self._db_path = Path(database_path).expanduser()
        if not self._db_path.is_absolute():
            self._db_path = Path.cwd() / self._db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    @property
    def database_path(self) -> str:
        return str(self._db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL DEFAULT '',
                    search_api TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS research_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    task_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    summary TEXT,
                    sources_summary TEXT,
                    notices_json TEXT NOT NULL DEFAULT '[]',
                    note_id TEXT,
                    note_path TEXT,
                    stream_token TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, task_index),
                    FOREIGN KEY(session_id) REFERENCES research_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS research_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    step INTEGER,
                    task_index INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES research_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS research_reports (
                    session_id INTEGER PRIMARY KEY,
                    report_markdown TEXT NOT NULL DEFAULT '',
                    note_id TEXT,
                    note_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES research_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS research_tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    event_id INTEGER,
                    task_index INTEGER,
                    agent TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result TEXT,
                    note_id TEXT,
                    note_path TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES research_sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_research_sessions_updated_at
                    ON research_sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_tasks_session_id
                    ON research_tasks(session_id);
                CREATE INDEX IF NOT EXISTS idx_research_steps_session_id
                    ON research_steps(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_research_tool_calls_session_id
                    ON research_tool_calls(session_id, id);
                """
            )

    def create_session(
        self,
        *,
        topic: str | None = None,
        search_api: str | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        normalized_topic = (topic or "").strip()
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_sessions (
                    topic, search_api, status, created_at, updated_at
                ) VALUES (?, ?, 'draft', ?, ?)
                """,
                (normalized_topic, search_api, now, now),
            )
            session_id = int(cursor.lastrowid)
        detail = self.get_session(session_id)
        if detail is None:
            raise RuntimeError("Failed to create research session")
        return detail

    def prepare_session_run(
        self,
        session_id: int,
        *,
        topic: str,
        search_api: str | None = None,
    ) -> dict[str, Any]:
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise ValueError("研究主题不能为空。")

        now = _utcnow()
        with self._lock, self._connection() as conn:
            existing = conn.execute(
                "SELECT status FROM research_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(session_id)

            if existing["status"] not in {"draft"}:
                raise ValueError("当前 session 已执行过研究，请创建新的研究 session。")

            conn.execute(
                """
                UPDATE research_sessions
                SET topic = ?, search_api = ?, status = 'running',
                    error_message = NULL, started_at = ?, completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (normalized_topic, search_api, now, now, session_id),
            )
            conn.execute("DELETE FROM research_tasks WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM research_steps WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM research_reports WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM research_tool_calls WHERE session_id = ?", (session_id,))

        detail = self.get_session(session_id)
        if detail is None:
            raise RuntimeError("Failed to prepare research session")
        return detail

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id,
                    s.topic,
                    s.search_api,
                    s.status,
                    s.error_message,
                    s.created_at,
                    s.updated_at,
                    s.started_at,
                    s.completed_at,
                    COALESCE(t.total_tasks, 0) AS total_tasks,
                    COALESCE(t.completed_tasks, 0) AS completed_tasks,
                    COALESCE(t.failed_tasks, 0) AS failed_tasks,
                    COALESCE(SUBSTR(r.report_markdown, 1, 160), '') AS report_excerpt
                FROM research_sessions s
                LEFT JOIN (
                    SELECT
                        session_id,
                        COUNT(*) AS total_tasks,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks
                    FROM research_tasks
                    GROUP BY session_id
                ) t ON t.session_id = s.id
                LEFT JOIN research_reports r ON r.session_id = s.id
                ORDER BY s.updated_at DESC, s.id DESC
                """
            ).fetchall()
        return [self._serialize_summary_row(row) for row in rows]

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            session_row = conn.execute(
                """
                SELECT id, topic, search_api, status, error_message,
                       created_at, updated_at, started_at, completed_at
                FROM research_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if session_row is None:
                return None

            task_rows = conn.execute(
                """
                SELECT task_index, title, intent, query, status, summary,
                       sources_summary, notices_json, note_id, note_path, stream_token
                FROM research_tasks
                WHERE session_id = ?
                ORDER BY task_index ASC
                """,
                (session_id,),
            ).fetchall()
            step_rows = conn.execute(
                """
                SELECT id, event_type, step, task_index, payload_json, created_at
                FROM research_steps
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
            tool_rows = conn.execute(
                """
                SELECT id, event_id, task_index, agent, tool, parameters_json,
                       result, note_id, note_path, created_at
                FROM research_tool_calls
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
            report_row = conn.execute(
                """
                SELECT report_markdown, note_id, note_path
                FROM research_reports
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        tasks = [self._serialize_task_row(row) for row in task_rows]
        steps = [self._serialize_step_row(row) for row in step_rows]
        tool_calls = [self._serialize_tool_row(row) for row in tool_rows]
        progress_logs = self._build_progress_logs(steps, tasks)

        detail = self._serialize_summary_row(
            self._summary_row_from_detail(session_row, tasks, report_row)
        )
        detail.update(
            {
                "report_markdown": report_row["report_markdown"] if report_row else "",
                "report_note_id": report_row["note_id"] if report_row else None,
                "report_note_path": report_row["note_path"] if report_row else None,
                "tasks": tasks,
                "steps": steps,
                "tool_calls": tool_calls,
                "progress_logs": progress_logs,
            }
        )
        return detail

    def record_event(self, session_id: int, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "unknown")
        task_index = self._int_or_none(event.get("task_id"))
        step_value = self._int_or_none(event.get("step"))
        payload_json = _json_dumps(event)
        now = _utcnow()

        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO research_steps (
                    session_id, event_type, step, task_index, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, event_type, step_value, task_index, payload_json, now),
            )
            conn.execute(
                "UPDATE research_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )

            if event_type == "todo_list":
                for task_payload in event.get("tasks") or []:
                    if isinstance(task_payload, dict):
                        self._upsert_task(conn, session_id, task_payload, now)
            elif event_type == "task_status":
                self._upsert_task(conn, session_id, event, now)
                status = str(event.get("status") or "").strip()
                if status == "failed":
                    conn.execute(
                        """
                        UPDATE research_sessions
                        SET status = 'failed', error_message = ?, updated_at = ?, completed_at = COALESCE(completed_at, ?)
                        WHERE id = ?
                        """,
                        (str(event.get("detail") or "任务执行失败"), now, now, session_id),
                    )
            elif event_type == "sources":
                self._ensure_task_row(conn, session_id, task_index, now)
                latest_sources = self._first_text(
                    event.get("latest_sources"),
                    event.get("sources_summary"),
                    event.get("raw_context"),
                )
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET sources_summary = COALESCE(?, sources_summary),
                        note_id = COALESCE(?, note_id),
                        note_path = COALESCE(?, note_path),
                        updated_at = ?
                    WHERE session_id = ? AND task_index = ?
                    """,
                    (
                        latest_sources,
                        self._clean_text(event.get("note_id")),
                        self._clean_text(event.get("note_path")),
                        now,
                        session_id,
                        task_index,
                    ),
                )
            elif event_type == "task_summary_chunk":
                self._ensure_task_row(conn, session_id, task_index, now)
                content = str(event.get("content") or "")
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET summary = COALESCE(summary, '') || ?,
                        note_id = COALESCE(?, note_id),
                        note_path = COALESCE(?, note_path),
                        updated_at = ?
                    WHERE session_id = ? AND task_index = ?
                    """,
                    (
                        content,
                        self._clean_text(event.get("note_id")),
                        self._clean_text(event.get("note_path")),
                        now,
                        session_id,
                        task_index,
                    ),
                )
            elif event_type == "tool_call":
                self._record_tool_call(conn, session_id, event, now)
            elif event_type == "report_note":
                self._upsert_report(
                    conn,
                    session_id,
                    report_markdown="",
                    note_id=self._clean_text(event.get("note_id")),
                    note_path=self._clean_text(event.get("note_path")),
                    now=now,
                )
            elif event_type == "final_report":
                self._upsert_report(
                    conn,
                    session_id,
                    report_markdown=str(event.get("report") or ""),
                    note_id=self._clean_text(event.get("note_id")),
                    note_path=self._clean_text(event.get("note_path")),
                    now=now,
                )
            elif event_type == "error":
                conn.execute(
                    """
                    UPDATE research_sessions
                    SET status = 'failed', error_message = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (str(event.get("detail") or "研究失败"), now, now, session_id),
                )
            elif event_type == "done":
                current = conn.execute(
                    "SELECT status FROM research_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if current and current["status"] != "failed":
                    conn.execute(
                        """
                        UPDATE research_sessions
                        SET status = 'completed', updated_at = ?, completed_at = COALESCE(completed_at, ?)
                        WHERE id = ?
                        """,
                        (now, now, session_id),
                    )
            elif event_type == "status" and task_index is not None:
                self._append_task_notice(conn, session_id, task_index, event, now)

    def _serialize_summary_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        topic = str(_row_value(row, "topic", "") or "")
        return {
            "id": int(_row_value(row, "id", 0)),
            "topic": topic,
            "display_topic": topic or "未命名研究",
            "search_api": _row_value(row, "search_api"),
            "status": _row_value(row, "status"),
            "error_message": _row_value(row, "error_message"),
            "created_at": _row_value(row, "created_at"),
            "updated_at": _row_value(row, "updated_at"),
            "started_at": _row_value(row, "started_at"),
            "completed_at": _row_value(row, "completed_at"),
            "total_tasks": int(_row_value(row, "total_tasks", 0) or 0),
            "completed_tasks": int(_row_value(row, "completed_tasks", 0) or 0),
            "failed_tasks": int(_row_value(row, "failed_tasks", 0) or 0),
            "report_excerpt": str(_row_value(row, "report_excerpt", "") or ""),
        }

    def _summary_row_from_detail(
        self,
        session_row: sqlite3.Row,
        tasks: list[dict[str, Any]],
        report_row: sqlite3.Row | None,
    ) -> dict[str, Any]:
        completed_tasks = sum(1 for task in tasks if task["status"] == "completed")
        failed_tasks = sum(1 for task in tasks if task["status"] == "failed")
        return {
            "id": session_row["id"],
            "topic": session_row["topic"],
            "search_api": session_row["search_api"],
            "status": session_row["status"],
            "error_message": session_row["error_message"],
            "created_at": session_row["created_at"],
            "updated_at": session_row["updated_at"],
            "started_at": session_row["started_at"],
            "completed_at": session_row["completed_at"],
            "total_tasks": len(tasks),
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "report_excerpt": (
                str(report_row["report_markdown"] or "")[:160] if report_row else ""
            ),
        }

    def _serialize_task_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["task_index"]),
            "title": row["title"],
            "intent": row["intent"],
            "query": row["query"],
            "status": row["status"],
            "summary": row["summary"] or "",
            "sources_summary": row["sources_summary"] or "",
            "notices": _json_loads(row["notices_json"], []),
            "note_id": row["note_id"],
            "note_path": row["note_path"],
            "stream_token": row["stream_token"],
        }

    def _serialize_step_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "type": row["event_type"],
            "step": row["step"],
            "task_id": row["task_index"],
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
        }

    def _serialize_tool_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "event_id": row["event_id"],
            "task_id": row["task_index"],
            "agent": row["agent"],
            "tool": row["tool"],
            "parameters": _json_loads(row["parameters_json"], {}),
            "result": row["result"] or "",
            "note_id": row["note_id"],
            "note_path": row["note_path"],
            "created_at": row["created_at"],
        }

    def _upsert_task(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        task_index = self._int_or_none(payload.get("task_id") or payload.get("id"))
        if task_index is None:
            return

        existing = conn.execute(
            """
            SELECT title, intent, query, status, summary, sources_summary,
                   notices_json, note_id, note_path, stream_token
            FROM research_tasks
            WHERE session_id = ? AND task_index = ?
            """,
            (session_id, task_index),
        ).fetchone()

        notices = _json_loads(existing["notices_json"], []) if existing else []
        merged_notices = notices
        if "notices" in payload and isinstance(payload["notices"], list):
            merged_notices = [str(item) for item in payload["notices"] if item]

        title = self._clean_text(payload.get("title")) or (
            existing["title"] if existing else f"任务 {task_index}"
        )
        intent = self._clean_text(payload.get("intent")) or (
            existing["intent"] if existing else ""
        )
        query = self._clean_text(payload.get("query")) or (
            existing["query"] if existing else ""
        )
        status = self._clean_text(payload.get("status")) or (
            existing["status"] if existing else "pending"
        )
        summary = self._clean_text(payload.get("summary"))
        if summary is None:
            summary = existing["summary"] if existing else ""
        sources_summary = self._first_text(
            payload.get("sources_summary"),
            payload.get("latest_sources"),
        )
        if sources_summary is None:
            sources_summary = existing["sources_summary"] if existing else ""
        note_id = self._clean_text(payload.get("note_id")) or (
            existing["note_id"] if existing else None
        )
        note_path = self._clean_text(payload.get("note_path")) or (
            existing["note_path"] if existing else None
        )
        stream_token = self._clean_text(payload.get("stream_token")) or (
            existing["stream_token"] if existing else None
        )

        conn.execute(
            """
            INSERT INTO research_tasks (
                session_id, task_index, title, intent, query, status, summary,
                sources_summary, notices_json, note_id, note_path, stream_token,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, task_index) DO UPDATE SET
                title = excluded.title,
                intent = excluded.intent,
                query = excluded.query,
                status = excluded.status,
                summary = excluded.summary,
                sources_summary = excluded.sources_summary,
                notices_json = excluded.notices_json,
                note_id = excluded.note_id,
                note_path = excluded.note_path,
                stream_token = excluded.stream_token,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                task_index,
                title,
                intent,
                query,
                status,
                summary,
                sources_summary,
                _json_dumps(merged_notices),
                note_id,
                note_path,
                stream_token,
                now,
                now,
            ),
        )

    def _ensure_task_row(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        task_index: int | None,
        now: str,
    ) -> None:
        if task_index is None:
            return
        conn.execute(
            """
            INSERT INTO research_tasks (
                session_id, task_index, title, intent, query, status,
                summary, sources_summary, notices_json, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', 'pending', '', '', '[]', ?, ?)
            ON CONFLICT(session_id, task_index) DO NOTHING
            """,
            (session_id, task_index, f"任务 {task_index}", now, now),
        )

    def _append_task_notice(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        task_index: int,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        message = self._clean_text(payload.get("message"))
        if not message:
            return
        self._ensure_task_row(conn, session_id, task_index, now)
        row = conn.execute(
            """
            SELECT notices_json
            FROM research_tasks
            WHERE session_id = ? AND task_index = ?
            """,
            (session_id, task_index),
        ).fetchone()
        notices = _json_loads(row["notices_json"] if row else None, [])
        notices.append(message)
        conn.execute(
            """
            UPDATE research_tasks
            SET notices_json = ?, updated_at = ?
            WHERE session_id = ? AND task_index = ?
            """,
            (_json_dumps(notices), now, session_id, task_index),
        )

    def _record_tool_call(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        task_index = self._int_or_none(payload.get("task_id"))

        conn.execute(
            """
            INSERT INTO research_tool_calls (
                session_id, event_id, task_index, agent, tool, parameters_json,
                result, note_id, note_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                self._int_or_none(payload.get("event_id")),
                task_index,
                str(payload.get("agent") or "Agent"),
                str(payload.get("tool") or "tool"),
                _json_dumps(payload.get("parameters") or {}),
                str(payload.get("result") or ""),
                self._clean_text(payload.get("note_id")),
                self._clean_text(payload.get("note_path")),
                now,
            ),
        )

        if task_index is not None:
            existing = conn.execute(
                """
                SELECT 1
                FROM research_tasks
                WHERE session_id = ? AND task_index = ?
                """,
                (session_id, task_index),
            ).fetchone()
            if not existing:
                return

            conn.execute(
                """
                UPDATE research_tasks
                SET note_id = COALESCE(?, note_id),
                    note_path = COALESCE(?, note_path),
                    updated_at = ?
                WHERE session_id = ? AND task_index = ?
                """,
                (
                    self._clean_text(payload.get("note_id")),
                    self._clean_text(payload.get("note_path")),
                    now,
                    session_id,
                    task_index,
                ),
            )

    def _upsert_report(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        *,
        report_markdown: str,
        note_id: str | None,
        note_path: str | None,
        now: str,
    ) -> None:
        existing = conn.execute(
            """
            SELECT report_markdown, note_id, note_path
            FROM research_reports
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

        report_text = report_markdown or (existing["report_markdown"] if existing else "")
        merged_note_id = note_id or (existing["note_id"] if existing else None)
        merged_note_path = note_path or (existing["note_path"] if existing else None)

        conn.execute(
            """
            INSERT INTO research_reports (
                session_id, report_markdown, note_id, note_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                report_markdown = excluded.report_markdown,
                note_id = excluded.note_id,
                note_path = excluded.note_path,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                report_text,
                merged_note_id,
                merged_note_path,
                now,
                now,
            ),
        )

    def _build_progress_logs(
        self,
        steps: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
    ) -> list[str]:
        task_map = {task["id"]: task for task in tasks}
        logs: list[str] = []

        for step in steps:
            payload = step["payload"]
            event_type = step["type"]
            task_id = step["task_id"]
            task = task_map.get(task_id)
            title = ""
            if isinstance(payload, dict):
                title = str(payload.get("title") or "") or (
                    task["title"] if task else f"任务 {task_id}" if task_id else ""
                )

            if event_type == "status":
                message = self._clean_text(payload.get("message"))
                if message:
                    logs.append(message)
            elif event_type == "todo_list":
                logs.append(
                    "已生成任务清单"
                    if (payload.get("tasks") or [])
                    else "未生成任务清单，使用默认任务继续"
                )
            elif event_type == "task_status":
                status = str(payload.get("status") or "")
                if status == "in_progress":
                    logs.append(f"开始执行任务：{title}")
                elif status == "completed":
                    logs.append(f"完成任务：{title}")
                elif status == "skipped":
                    logs.append(f"任务跳过：{title}")
                elif status == "failed":
                    logs.append(f"任务失败：{title}")
            elif event_type == "sources":
                if self._first_text(
                    payload.get("latest_sources"),
                    payload.get("sources_summary"),
                    payload.get("raw_context"),
                ):
                    logs.append(f"已更新任务来源：{title or f'任务 {task_id}'}")
                backend = self._clean_text(payload.get("backend"))
                if backend:
                    logs.append(f"当前使用搜索后端：{backend}")
            elif event_type == "tool_call":
                agent = self._clean_text(payload.get("agent")) or "Agent"
                tool = self._clean_text(payload.get("tool")) or "tool"
                logs.append(f"{agent} 调用了 {tool}")
            elif event_type == "final_report":
                logs.append("最终报告已生成")
            elif event_type == "error":
                logs.append("研究失败，已停止流程")

        return logs

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            text = ResearchSessionStore._clean_text(value)
            if text:
                return text
        return None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None
