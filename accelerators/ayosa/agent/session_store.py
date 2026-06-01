"""Conversational session memory for AYOSA.

Stores rich, per-session context that the agent can use to infer
follow-up questions (e.g. carrying `service` and `time_range` from the
previous turn). The store is process-local by default; if a `persist_dir`
is supplied, each upsert is written atomically to
`{persist_dir}/{session_id}.json`.

Design choices:
  * Threadsafe — uses a single `Lock`. Adequate for a single uvicorn
    worker; swap for Redis/SQL by re-implementing this class without
    touching callers.
  * Pure dataclass-like records (Pydantic v2). Easy to JSON-serialise.
  * Strict session isolation: every read/write is keyed by `session_id`
    and never iterates across sessions on the hot path. (Enforced by
    tests.)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────── #
# Records
# ──────────────────────────────────────────────────────────────────────── #
class SessionMessage(BaseModel):
    role: str            # "user" | "assistant"
    content: str
    ts: str              # ISO-8601 UTC
    intent: Optional[str] = None


class EvidenceSummary(BaseModel):
    """Compact summary of last-run evidence — never the raw blobs."""
    total: int = 0
    ok: int = 0
    errors: int = 0
    signals: list[str] = []
    top_findings: list[str] = []


class SessionRecord(BaseModel):
    session_id: str
    created_at: str
    updated_at: str
    messages: list[SessionMessage] = Field(default_factory=list)
    last_intent: Optional[str] = None
    last_service: Optional[str] = None
    last_time_range: Optional[str] = None
    last_tools_used: list[str] = Field(default_factory=list)
    last_evidence_summary: Optional[EvidenceSummary] = None
    last_snapshot: Optional[dict[str, Any]] = None


# ──────────────────────────────────────────────────────────────────────── #
# Store
# ──────────────────────────────────────────────────────────────────────── #
class SessionStore:
    """In-memory session store with optional JSON persistence."""

    def __init__(
        self,
        persist_dir: Optional[Path | str] = None,
        max_messages_per_session: int = 100,
    ) -> None:
        self._lock = Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._max_messages = max_messages_per_session
        self._persist_dir: Optional[Path] = (
            Path(persist_dir) if persist_dir else None
        )
        if self._persist_dir is not None:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_all()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def new_session_id(self) -> str:
        """Generate a fresh, opaque session_id."""
        return uuid.uuid4().hex

    def get(self, session_id: str) -> Optional[SessionRecord]:
        """Return the record or None — does NOT auto-create."""
        if not session_id:
            return None
        with self._lock:
            rec = self._sessions.get(session_id)
            return rec.model_copy(deep=True) if rec is not None else None

    def reset(self, session_id: str) -> None:
        """Drop a single session (memory + disk). No-op if unknown."""
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)
            self._delete_file(session_id)

    def clear(self) -> None:
        """Drop ALL sessions. Test helper."""
        with self._lock:
            self._sessions.clear()
            if self._persist_dir and self._persist_dir.exists():
                for f in self._persist_dir.glob("*.json"):
                    try:
                        f.unlink()
                    except OSError:
                        pass

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    # ------------------------------------------------------------------ #
    # High-level upserts
    # ------------------------------------------------------------------ #
    def record_turn(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_answer: str,
        intent: Optional[str] = None,
        service: Optional[str] = None,
        time_range: Optional[str] = None,
        tools_used: Optional[list[str]] = None,
        evidence_summary: Optional[EvidenceSummary] = None,
        snapshot: Optional[dict[str, Any]] = None,
    ) -> SessionRecord:
        """Append one round-trip + refresh last_* fields. Persists if configured."""
        now = _utc_now_iso()
        with self._lock:
            rec = self._sessions.get(session_id)
            if rec is None:
                rec = SessionRecord(
                    session_id=session_id,
                    created_at=now,
                    updated_at=now,
                )
                self._sessions[session_id] = rec

            rec.messages.append(
                SessionMessage(role="user", content=user_message, ts=now, intent=intent)
            )
            rec.messages.append(
                SessionMessage(role="assistant", content=assistant_answer, ts=now)
            )
            # Trim oldest if past the cap
            if len(rec.messages) > self._max_messages:
                rec.messages = rec.messages[-self._max_messages :]

            if intent:
                rec.last_intent = intent
            if service is not None:
                rec.last_service = service
            if time_range:
                rec.last_time_range = time_range
            if tools_used is not None:
                rec.last_tools_used = list(tools_used)
            if evidence_summary is not None:
                rec.last_evidence_summary = evidence_summary
            if snapshot is not None:
                rec.last_snapshot = snapshot

            rec.updated_at = now
            self._save_file(rec)
            return rec.model_copy(deep=True)

    # ------------------------------------------------------------------ #
    # Inference helper — pure, exported for tests
    # ------------------------------------------------------------------ #
    def infer_followup_context(
        self,
        session_id: str,
        *,
        current_service: Optional[str],
        current_time_range: Optional[str],
        default_time_range: str = "30m",
    ) -> dict[str, Optional[str]]:
        """Fill blanks from the prior turn, never overriding user-provided values.

        Returns `{service, time_range}` with the inferred values.
        """
        rec = self.get(session_id)
        if rec is None:
            return {
                "service": current_service,
                "time_range": current_time_range or default_time_range,
            }

        service = current_service if current_service else rec.last_service
        # Only inherit a time_range when the caller didn't override (i.e.
        # they sent the model default or left it blank).
        tr = current_time_range
        if (not tr or tr == default_time_range) and rec.last_time_range:
            tr = rec.last_time_range
        return {"service": service, "time_range": tr or default_time_range}

    # ------------------------------------------------------------------ #
    # Persistence — internal
    # ------------------------------------------------------------------ #
    def _file_for(self, session_id: str) -> Optional[Path]:
        if self._persist_dir is None:
            return None
        # Defence: only allow opaque ids — refuse path-traversal characters.
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        if not safe or safe != session_id:
            return None
        return self._persist_dir / f"{safe}.json"

    def _save_file(self, rec: SessionRecord) -> None:
        path = self._file_for(rec.session_id)
        if path is None:
            return
        try:
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=f".{rec.session_id}.", suffix=".json", dir=str(path.parent)
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(rec.model_dump(), f, default=str)
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as exc:  # noqa: BLE001 — never fail caller on disk error
            logger.warning("Session persist failed for %s: %s", rec.session_id, exc)

    def _delete_file(self, session_id: str) -> None:
        path = self._file_for(session_id)
        if path and path.exists():
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Session delete failed for %s: %s", session_id, exc)

    def _load_all(self) -> None:
        assert self._persist_dir is not None
        for path in self._persist_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rec = SessionRecord(**data)
                self._sessions[rec.session_id] = rec
            except Exception as exc:  # noqa: BLE001 — bad files must not crash boot
                logger.warning("Skipping unreadable session file %s: %s", path, exc)


# ──────────────────────────────────────────────────────────────────────── #
# Module-level singleton — used by the chat bridge so a single uvicorn
# worker shares memory across requests. Tests inject their own store.
# ──────────────────────────────────────────────────────────────────────── #
_DEFAULT_PERSIST_DIR = Path("runtime") / "ayosa_sessions"
_default_store: Optional[SessionStore] = None
_default_lock = Lock()


def get_default_store() -> SessionStore:
    """Return the process-wide store (lazily initialised)."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = SessionStore(persist_dir=_DEFAULT_PERSIST_DIR)
        return _default_store


def set_default_store(store: Optional[SessionStore]) -> None:
    """Test helper: install (or clear) the process-wide store."""
    global _default_store
    with _default_lock:
        _default_store = store


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarise_evidence(observations: list[Any]) -> EvidenceSummary:
    """Build a compact summary from a list of `Observation`s or dicts."""
    total = ok = errors = 0
    signals: list[str] = []
    top: list[str] = []
    for o in observations:
        status = getattr(o, "status", None) or (
            o.get("status") if isinstance(o, dict) else None
        )
        signal = getattr(o, "signal", None) or (
            o.get("signal") if isinstance(o, dict) else None
        )
        finding = getattr(o, "finding", None) or (
            o.get("finding") if isinstance(o, dict) else None
        )
        total += 1
        if status == "ok":
            ok += 1
            if finding and len(top) < 5:
                top.append(finding)
        elif status == "error":
            errors += 1
        if signal and signal not in signals:
            signals.append(signal)
    return EvidenceSummary(
        total=total, ok=ok, errors=errors, signals=signals, top_findings=top
    )


__all__ = [
    "SessionStore",
    "SessionRecord",
    "SessionMessage",
    "EvidenceSummary",
    "get_default_store",
    "set_default_store",
    "summarise_evidence",
]
