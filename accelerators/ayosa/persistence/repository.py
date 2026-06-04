"""Repository for AYOSA run history.

All public methods are best-effort: any sqlite error is logged and the
method returns a benign empty value (None / [] / RunComparison()) so the
chat path is never broken by a persistence issue.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from accelerators.ayosa.persistence.db import Database, init_db
from accelerators.ayosa.persistence.models import (
    PersistedRun,
    RunComparison,
    RunSummary,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────── #
# Module-level default singleton (opt-in)
# ──────────────────────────────────────────────────────────────────────── #
_DEFAULT_REPO: Optional["Repository"] = None
_DEFAULT_EXPLICIT: bool = False  # True once set_default_repository was called
_DEFAULT_LOCK = threading.Lock()


def get_default_repository() -> Optional["Repository"]:
    """Return the process-wide repository, creating it lazily.

    Returns None if the DB cannot be initialised, or if someone called
    `set_default_repository(None)` (used by tests to simulate persistence
    being unavailable). Callers must treat persistence as optional.
    """
    global _DEFAULT_REPO
    with _DEFAULT_LOCK:
        if _DEFAULT_EXPLICIT:
            return _DEFAULT_REPO
        if _DEFAULT_REPO is not None:
            return _DEFAULT_REPO
        try:
            _DEFAULT_REPO = Repository(init_db())
        except Exception as exc:  # noqa: BLE001 — persistence is optional
            logger.warning("AYOSA persistence disabled: %s", exc)
            _DEFAULT_REPO = None
        return _DEFAULT_REPO


def set_default_repository(repo: Optional["Repository"]) -> None:
    """Inject a repository (for tests) or clear it (`None`).

    Once called with `None`, lazy initialisation is suppressed until
    another non-None value is supplied.
    """
    global _DEFAULT_REPO, _DEFAULT_EXPLICIT
    with _DEFAULT_LOCK:
        _DEFAULT_REPO = repo
        _DEFAULT_EXPLICIT = True


# ──────────────────────────────────────────────────────────────────────── #
# Repository
# ──────────────────────────────────────────────────────────────────────── #
class Repository:
    """SQLite-backed repository for AYOSA runs."""

    def __init__(self, database: Database):
        self.db = database

    # ── Writes ───────────────────────────────────────────────────────── #
    def create_run(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        message: str = "",
        intent: str | None = None,
        service: str | None = None,
        time_range: str | None = None,
        tools_used: Iterable[str] | None = None,
        confidence: float = 0.0,
        answer: str = "",
        snapshot: dict[str, Any] | None = None,
        evidence_summary: dict[str, Any] | None = None,
        tool_steps: Iterable[dict[str, Any]] | None = None,
        created_at: str | None = None,
        iterations: int = 1,
        replan_reason: str | None = None,
        loop_summary: dict[str, Any] | None = None,
    ) -> str | None:
        """Persist a single run; returns its `run_id`, or None on failure."""
        rid = (run_id or f"run-{uuid.uuid4().hex[:12]}").strip()
        ts = created_at or datetime.now(timezone.utc).isoformat()
        tools_list = list(tools_used or [])
        steps_list = list(tool_steps or [])

        try:
            with self.db.connect() as conn:
                if session_id:
                    self._touch_session(conn, session_id, ts)

                conn.execute(
                    """
                    INSERT OR REPLACE INTO ayosa_runs (
                        run_id, session_id, message, intent, service,
                        time_range, tools_used_json, confidence, answer,
                        snapshot_json, evidence_summary_json, created_at,
                        iterations, replan_reason, loop_summary_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rid, session_id, message, intent, service,
                        time_range, json.dumps(tools_list),
                        float(confidence or 0.0), answer or "",
                        json.dumps(snapshot) if snapshot is not None else None,
                        json.dumps(evidence_summary)
                        if evidence_summary is not None else None,
                        ts,
                        max(1, int(iterations or 1)),
                        replan_reason,
                        json.dumps(loop_summary) if loop_summary is not None else None,
                    ),
                )

                # Reset per-run child rows (idempotent overwrite).
                conn.execute(
                    "DELETE FROM ayosa_tool_steps WHERE run_id = ?", (rid,)
                )
                for i, step in enumerate(steps_list):
                    conn.execute(
                        """
                        INSERT INTO ayosa_tool_steps
                            (run_id, step_index, tool, label, status, error, iteration)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rid,
                            int(step.get("index", i)),
                            str(step.get("tool", "")),
                            step.get("label"),
                            step.get("status"),
                            step.get("error"),
                            int(step.get("iteration", 0) or 0),
                        ),
                    )

                if snapshot is not None:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ayosa_snapshots (
                            run_id, root_cause, impact, confidence,
                            coverage_json, top_findings_json,
                            recommended_actions_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rid,
                            snapshot.get("root_cause", "") or "",
                            snapshot.get("impact", "") or "",
                            float(snapshot.get("confidence", 0.0) or 0.0),
                            json.dumps(snapshot.get("coverage", {}) or {}),
                            json.dumps(snapshot.get("top_findings", []) or []),
                            json.dumps(
                                snapshot.get("recommended_actions", []) or []
                            ),
                        ),
                    )

                if message:
                    conn.execute(
                        """
                        INSERT INTO ayosa_messages
                            (session_id, run_id, role, content, created_at)
                        VALUES (?, ?, 'user', ?, ?)
                        """,
                        (session_id, rid, message, ts),
                    )
                if answer:
                    conn.execute(
                        """
                        INSERT INTO ayosa_messages
                            (session_id, run_id, role, content, created_at)
                        VALUES (?, ?, 'assistant', ?, ?)
                        """,
                        (session_id, rid, answer, ts),
                    )
            return rid
        except sqlite3.Error as exc:
            logger.warning("create_run failed: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 — never raise into chat path
            logger.warning("create_run unexpected failure: %s", exc)
            return None

    # ── Reads ────────────────────────────────────────────────────────── #
    def get_run(self, run_id: str) -> Optional[PersistedRun]:
        if not run_id:
            return None
        try:
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM ayosa_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    return None

                steps = conn.execute(
                    """
                    SELECT step_index, tool, label, status, error, iteration
                      FROM ayosa_tool_steps
                     WHERE run_id = ?
                  ORDER BY iteration ASC, step_index ASC
                    """,
                    (run_id,),
                ).fetchall()

                return PersistedRun(
                    run_id=row["run_id"],
                    session_id=row["session_id"],
                    message=row["message"] or "",
                    intent=row["intent"],
                    service=row["service"],
                    time_range=row["time_range"],
                    tools_used=_loads_list(row["tools_used_json"]),
                    confidence=float(row["confidence"] or 0.0),
                    answer=row["answer"] or "",
                    snapshot=_loads_dict_or_none(row["snapshot_json"]),
                    evidence_summary=_loads_dict_or_none(
                        row["evidence_summary_json"]
                    ),
                    tool_steps=[
                        {
                            "index": s["step_index"],
                            "tool": s["tool"],
                            "label": s["label"],
                            "status": s["status"],
                            "error": s["error"],
                            "iteration": int(s["iteration"] or 0),
                        }
                        for s in steps
                    ],
                    created_at=row["created_at"] or "",
                    iterations=int(_row_get(row, "iterations", 1) or 1),
                    replan_reason=_row_get(row, "replan_reason", None),
                    loop_summary=_loads_dict_or_none(
                        _row_get(row, "loop_summary_json", None)
                    ),
                )
        except sqlite3.Error as exc:
            logger.warning("get_run(%s) failed: %s", run_id, exc)
            return None

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        service: str | None = None,
        limit: int = 50,
    ) -> list[RunSummary]:
        try:
            limit_i = max(1, min(int(limit or 50), 500))
        except (TypeError, ValueError):
            limit_i = 50

        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if service:
            clauses.append("service = ?")
            params.append(service)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        try:
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT run_id, session_id, message, intent, service,
                           time_range, tools_used_json, confidence, created_at,
                           iterations
                      FROM ayosa_runs
                      {where}
                  ORDER BY datetime(created_at) DESC, run_id DESC
                     LIMIT ?
                    """,
                    (*params, limit_i),
                ).fetchall()
            return [
                RunSummary(
                    run_id=r["run_id"],
                    session_id=r["session_id"],
                    message=r["message"] or "",
                    intent=r["intent"],
                    service=r["service"],
                    time_range=r["time_range"],
                    tools_used=_loads_list(r["tools_used_json"]),
                    confidence=float(r["confidence"] or 0.0),
                    created_at=r["created_at"] or "",
                    iterations=int(_row_get(r, "iterations", 1) or 1),
                )
                for r in rows
            ]
        except sqlite3.Error as exc:
            logger.warning("list_runs failed: %s", exc)
            return []

    def compare_runs(self, run_id_a: str, run_id_b: str) -> RunComparison:
        left = self.get_run(run_id_a) if run_id_a else None
        right = self.get_run(run_id_b) if run_id_b else None

        missing: list[str] = []
        if run_id_a and left is None:
            missing.append(run_id_a)
        if run_id_b and right is None:
            missing.append(run_id_b)

        diffs: dict[str, Any] = {}
        if left is not None and right is not None:
            diffs = _diff_runs(left, right)

        return RunComparison(
            left=left, right=right,
            missing_run_ids=missing, differences=diffs,
        )

    # ── Internal ─────────────────────────────────────────────────────── #
    @staticmethod
    def _touch_session(conn: sqlite3.Connection, session_id: str, ts: str) -> None:
        conn.execute(
            """
            INSERT INTO ayosa_sessions (session_id, created_at, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (session_id, ts, ts),
        )

    # Step 21: targeted update so the SSE ``loop_summary`` event can be
    # recorded after the row has already been created (or independently of
    # the chat-response persist path). Idempotent and best-effort.
    def record_loop_summary(
        self, run_id: str, summary: dict[str, Any] | None
    ) -> bool:
        if not run_id:
            return False
        payload = json.dumps(summary) if summary is not None else None
        try:
            with self.db.connect() as conn:
                cur = conn.execute(
                    "UPDATE ayosa_runs SET loop_summary_json = ? WHERE run_id = ?",
                    (payload, run_id),
                )
                return cur.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("record_loop_summary(%s) failed: %s", run_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001 — never break chat
            logger.warning(
                "record_loop_summary(%s) unexpected failure: %s", run_id, exc
            )
            return False


# ──────────────────────────────────────────────────────────────────────── #
# Convenience: persist an AgentResult-shaped chat response
# ──────────────────────────────────────────────────────────────────────── #
def persist_agent_result(
    chat_response: dict[str, Any],
    *,
    request_message: str,
    repo: Repository | None = None,
) -> str | None:
    """Persist a chat-response dict via the default (or supplied) repo.

    Returns the new run_id, or None when persistence is unavailable or
    fails. Safe to call from the chat path — never raises.
    """
    try:
        repository = repo if repo is not None else get_default_repository()
        if repository is None:
            return None

        snapshot = chat_response.get("incident_snapshot")
        tools_used = (
            (chat_response.get("plan") or {}).get("selected_tools") or []
        )
        return repository.create_run(
            session_id=chat_response.get("session_id"),
            message=request_message or "",
            intent=chat_response.get("intent"),
            service=chat_response.get("service"),
            time_range=chat_response.get("time_range"),
            tools_used=tools_used,
            confidence=float(chat_response.get("confidence") or 0.0),
            answer=chat_response.get("answer") or "",
            snapshot=snapshot if isinstance(snapshot, dict) else None,
            evidence_summary={
                "missing_signals": chat_response.get("missing_signals") or [],
                "signal_coverage": chat_response.get("signal_coverage") or {},
                "evidence_count": len(chat_response.get("evidence") or []),
            },
            tool_steps=chat_response.get("tool_steps") or [],
            iterations=int(chat_response.get("iterations") or 1),
            replan_reason=chat_response.get("replan_reason"),
            loop_summary=(
                chat_response.get("loop_summary")
                if isinstance(chat_response.get("loop_summary"), dict)
                else None
            ),
        )
    except Exception as exc:  # noqa: BLE001 — never break chat
        logger.warning("persist_agent_result failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _loads_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _loads_dict_or_none(raw: Any) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def _row_get(row: Any, key: str, default: Any) -> Any:
    """Tolerant accessor: pre-Step-16 rows may lack new columns."""
    try:
        if key in row.keys():
            return row[key]
    except Exception:  # noqa: BLE001
        pass
    return default


def _trajectory_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group tool steps by iteration so two runs can be compared per pass."""
    by_it: dict[int, list[dict[str, Any]]] = {}
    for s in steps or []:
        it = int(s.get("iteration", 0) or 0)
        by_it.setdefault(it, []).append(s)
    out: list[dict[str, Any]] = []
    for it in sorted(by_it):
        tools = [s.get("tool") for s in by_it[it] if s.get("tool")]
        statuses = [s.get("status") for s in by_it[it]]
        out.append({
            "iteration": it,
            "tools": tools,
            "statuses": statuses,
        })
    return out


def _diff_runs(a: PersistedRun, b: PersistedRun) -> dict[str, Any]:
    fields = (
        "intent", "service", "time_range", "confidence",
        "answer", "tools_used",
    )
    out: dict[str, Any] = {}
    for f in fields:
        va, vb = getattr(a, f), getattr(b, f)
        if va != vb:
            out[f] = {"left": va, "right": vb}

    # Snapshot field-level diff (compact)
    snap_a = a.snapshot or {}
    snap_b = b.snapshot or {}
    snap_diff: dict[str, Any] = {}
    for k in ("root_cause", "impact", "confidence",
              "top_findings", "recommended_actions"):
        if snap_a.get(k) != snap_b.get(k):
            snap_diff[k] = {"left": snap_a.get(k), "right": snap_b.get(k)}
    if snap_diff:
        out["snapshot"] = snap_diff

    # Step 16: trajectory diff — iteration count + per-pass tool sets.
    traj_a = _trajectory_from_steps(a.tool_steps)
    traj_b = _trajectory_from_steps(b.tool_steps)
    traj_diff: dict[str, Any] = {}
    if a.iterations != b.iterations:
        traj_diff["iterations"] = {"left": a.iterations, "right": b.iterations}
    if traj_a != traj_b:
        traj_diff["passes"] = {"left": traj_a, "right": traj_b}
        # Tools added on right but not on left, per iteration.
        added: list[dict[str, Any]] = []
        for pass_b in traj_b:
            left_pass = next(
                (p for p in traj_a if p["iteration"] == pass_b["iteration"]),
                None,
            )
            left_tools = set(left_pass["tools"]) if left_pass else set()
            new_tools = [t for t in pass_b["tools"] if t not in left_tools]
            if new_tools:
                added.append({
                    "iteration": pass_b["iteration"],
                    "new_tools": new_tools,
                })
        if added:
            traj_diff["new_tools_per_pass"] = added
    if (a.replan_reason or "") != (b.replan_reason or ""):
        traj_diff["replan_reason"] = {
            "left": a.replan_reason,
            "right": b.replan_reason,
        }
    # Step 21: loop_summary diff lives inside trajectory so consumers only
    # need to look at one bucket for re-plan-related deltas.
    if (a.loop_summary or None) != (b.loop_summary or None):
        traj_diff["loop_summary"] = {
            "left": a.loop_summary,
            "right": b.loop_summary,
        }
    if traj_diff:
        out["trajectory"] = traj_diff

    return out


__all__ = [
    "Repository",
    "get_default_repository",
    "persist_agent_result",
    "set_default_repository",
]
