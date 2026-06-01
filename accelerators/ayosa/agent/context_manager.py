"""Conversation context manager.

In-memory store of past turns, keyed by `session_id`. Thread-safe enough
for a single-process FastAPI worker; replace with Redis / DB in a future
revision without changing the public surface.
"""

from __future__ import annotations

from threading import Lock
from typing import Iterable

from accelerators.ayosa.agent.schemas import ConversationState, ConversationTurn


class ContextManager:
    """Per-session conversation history."""

    def __init__(self, max_turns_per_session: int = 50) -> None:
        self._lock = Lock()
        self._sessions: dict[str, ConversationState] = {}
        self._max_turns = max_turns_per_session

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #
    def get(self, session_id: str) -> ConversationState:
        """Return (or lazily create) the conversation state."""
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = ConversationState(session_id=session_id, turns=[])
                self._sessions[session_id] = state
            return state

    def append(self, session_id: str, turn: ConversationTurn) -> ConversationState:
        """Append one turn; trim oldest if the cap is exceeded."""
        with self._lock:
            state = self._sessions.setdefault(
                session_id, ConversationState(session_id=session_id, turns=[])
            )
            state.turns.append(turn)
            if len(state.turns) > self._max_turns:
                state.turns = state.turns[-self._max_turns :]
            return state

    def history(self, session_id: str) -> list[ConversationTurn]:
        return list(self.get(session_id).turns)

    def reset(self, session_id: str | None = None) -> None:
        """Clear one session, or all sessions if `session_id` is None."""
        with self._lock:
            if session_id is None:
                self._sessions.clear()
            else:
                self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def sessions(self) -> Iterable[str]:
        with self._lock:
            return list(self._sessions.keys())


__all__ = ["ContextManager"]
