"""SQLite connection + schema bootstrap for AYOSA persistence.

The DB is intentionally simple: one file at `runtime/ayosa.db`. Every
call goes through a short-lived connection (no pooling) so the
repository is safe to use from sync FastAPI handlers and tests alike.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


DEFAULT_DB_PATH: Path = Path("runtime") / "ayosa.db"

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS ayosa_sessions (
        session_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ayosa_messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        run_id TEXT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES ayosa_sessions(session_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ayosa_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT,
        message TEXT NOT NULL DEFAULT '',
        intent TEXT,
        service TEXT,
        time_range TEXT,
        tools_used_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0.0,
        answer TEXT NOT NULL DEFAULT '',
        snapshot_json TEXT,
        evidence_summary_json TEXT,
        created_at TEXT NOT NULL,
        iterations INTEGER NOT NULL DEFAULT 1,
        replan_reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ayosa_tool_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        tool TEXT NOT NULL,
        label TEXT,
        status TEXT,
        error TEXT,
        iteration INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(run_id) REFERENCES ayosa_runs(run_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ayosa_snapshots (
        run_id TEXT PRIMARY KEY,
        root_cause TEXT NOT NULL DEFAULT '',
        impact TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.0,
        coverage_json TEXT NOT NULL DEFAULT '{}',
        top_findings_json TEXT NOT NULL DEFAULT '[]',
        recommended_actions_json TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY(run_id) REFERENCES ayosa_runs(run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_runs_session   ON ayosa_runs(session_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_service   ON ayosa_runs(service)",
    "CREATE INDEX IF NOT EXISTS ix_runs_created   ON ayosa_runs(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_msgs_session   ON ayosa_messages(session_id)",
    "CREATE INDEX IF NOT EXISTS ix_tool_steps_run ON ayosa_tool_steps(run_id)",
)

# Step 16: idempotent column additions for pre-Step-16 databases.
_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE ayosa_runs ADD COLUMN iterations INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE ayosa_runs ADD COLUMN replan_reason TEXT",
    "ALTER TABLE ayosa_tool_steps ADD COLUMN iteration INTEGER NOT NULL DEFAULT 0",
)


class Database:
    """Thin SQLite wrapper. One file per process; per-call connections."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ensure_parent()
        self._bootstrap()

    # ── Public ───────────────────────────────────────────────────────── #
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection with sensible defaults."""
        conn = sqlite3.connect(
            str(self.path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Internal ─────────────────────────────────────────────────────── #
    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _bootstrap(self) -> None:
        with self._lock, self.connect() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            # Step 16: backward-compat migrations for trajectory persistence.
            # SQLite ALTER TABLE ADD COLUMN is idempotent only when guarded —
            # we ignore "duplicate column" errors so existing DBs upgrade in place.
            for stmt in _MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise


def init_db(path: str | Path | None = None) -> Database:
    """Initialise (or reuse) a SQLite database at the given path."""
    db_path = Path(path) if path else DEFAULT_DB_PATH
    return Database(db_path)


__all__ = ["DEFAULT_DB_PATH", "Database", "init_db"]
