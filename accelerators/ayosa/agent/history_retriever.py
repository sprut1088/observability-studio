"""Prior-run retrieval for the AYOSA agent.

Surfaces a compact list of previously persisted investigation runs that
look related to the current message. The result is attached to the plan
alongside ``workspace_context`` so the synthesiser / LLM context and the
UI can cite past work without re-running the same probes.

Design rules:

* **Best-effort only.** Persistence is optional. Any failure returns
  ``{"available": False, "message": "..."}`` so the chat path keeps
  working.
* **No fabrication.** We never invent runs; we only return rows that
  the repository actually persisted.
* **Pure-Python retriever.** Token-overlap scoring against the stored
  ``message`` field, with a small boost when ``service`` matches the
  current request. The same retriever can be replaced by a vector
  index later without changing the call sites.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

from accelerators.ayosa.persistence import (
    Repository,
    RunSummary,
    get_default_repository,
)

logger = logging.getLogger(__name__)

# Token splitter mirrors the workspace-index retriever so the two
# subsystems behave consistently.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

# Cap on rows scanned per call. The repository itself caps at 500.
_SCAN_LIMIT = 200


def retrieve_prior_runs(
    *,
    message: str,
    service: Optional[str] = None,
    intent: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 5,
    repo: Optional[Repository] = None,
) -> dict[str, Any]:
    """Return a compact payload of related prior runs.

    The shape mirrors the workspace-context payload:

    .. code-block:: python

        {
            "available": True,
            "matches": [
                {
                    "run_id": "...",
                    "message": "...",
                    "intent": "...",
                    "service": "...",
                    "time_range": "30m",
                    "confidence": 0.74,
                    "created_at": "2026-...",
                    "tools_used": ["prometheus", "loki"],
                    "score": 1.2,
                    "matched_on": ["service", "tokens"],
                },
                ...
            ],
            "scanned": 27,
            "count": 5,
        }
    """
    try:
        repository = repo if repo is not None else get_default_repository()
        if repository is None:
            return {
                "available": False,
                "message": "run history unavailable",
                "matches": [],
                "count": 0,
            }

        try:
            limit_i = max(1, min(int(limit or 5), 25))
        except (TypeError, ValueError):
            limit_i = 5

        rows = _gather_candidates(
            repository,
            service=service,
            session_id=session_id,
        )
        if not rows:
            return {
                "available": True,
                "matches": [],
                "count": 0,
                "scanned": 0,
            }

        q_tokens = _tokenise(message)
        service_l = (service or "").strip().lower()
        intent_l = (intent or "").strip().lower()

        scored: list[tuple[float, list[str], RunSummary]] = []
        for r in rows:
            score, matched = _score_run(
                r,
                q_tokens=q_tokens,
                service_l=service_l,
                intent_l=intent_l,
            )
            if score <= 0:
                continue
            scored.append((score, matched, r))

        scored.sort(key=lambda x: (-x[0], _sort_key_created_at(x[2])))
        top = scored[:limit_i]

        return {
            "available": True,
            "matches": [_row_to_payload(r, score, matched)
                        for score, matched, r in top],
            "count": len(top),
            "scanned": len(rows),
        }
    except Exception as exc:  # noqa: BLE001 — never break chat
        logger.warning("retrieve_prior_runs failed: %s", exc)
        return {
            "available": False,
            "message": "run history unavailable",
            "matches": [],
            "count": 0,
        }


# ──────────────────────────────────────────────────────────────────────── #
# Internals
# ──────────────────────────────────────────────────────────────────────── #
def _gather_candidates(
    repository: Repository,
    *,
    service: Optional[str],
    session_id: Optional[str],
) -> list[RunSummary]:
    """Fetch a working set of recent rows.

    Strategy:
      1. If ``service`` is set, fetch up to ``_SCAN_LIMIT`` for that service.
      2. Always merge in the most recent global rows so cross-service
         questions still find context.
      3. Optionally include same-session rows.
    Duplicates (by ``run_id``) are removed; insertion order preserved.
    """
    seen: set[str] = set()
    out: list[RunSummary] = []

    def _extend(rows: Iterable[RunSummary]) -> None:
        for r in rows:
            if r.run_id and r.run_id not in seen:
                seen.add(r.run_id)
                out.append(r)

    if service:
        _extend(repository.list_runs(service=service, limit=_SCAN_LIMIT))
    if session_id:
        _extend(repository.list_runs(session_id=session_id, limit=50))
    # Always include a tail of globally-recent runs so non-service queries
    # still surface something useful.
    _extend(repository.list_runs(limit=50))
    return out


def _score_run(
    row: RunSummary,
    *,
    q_tokens: list[str],
    service_l: str,
    intent_l: str,
) -> tuple[float, list[str]]:
    """Token-overlap + service/intent boosts.

    Returns (score, matched_on). A score of 0 means "do not include".
    """
    score = 0.0
    matched: list[str] = []

    row_service_l = (row.service or "").lower()
    if service_l and row_service_l and row_service_l == service_l:
        score += 1.0
        matched.append("service")

    if intent_l and (row.intent or "").lower() == intent_l:
        score += 0.5
        matched.append("intent")

    if q_tokens:
        row_tokens = set(_tokenise(row.message))
        hits = sum(1 for t in q_tokens if t in row_tokens)
        if hits:
            overlap = hits / max(len(q_tokens), 1)
            score += overlap
            matched.append("tokens")

    return score, matched


def _row_to_payload(
    row: RunSummary, score: float, matched: list[str],
) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "message": row.message or "",
        "intent": row.intent,
        "service": row.service,
        "time_range": row.time_range,
        "confidence": float(row.confidence or 0.0),
        "created_at": row.created_at or "",
        "tools_used": list(row.tools_used or []),
        "score": round(float(score), 3),
        "matched_on": matched,
    }


def _tokenise(text: str) -> list[str]:
    return [m.lower() for m in _TOKEN_RE.findall(text or "")]


def _sort_key_created_at(row: RunSummary) -> str:
    # Newer rows should win tie-breakers; sort descending on created_at by
    # inverting via a tuple sort. We return the negative-time string
    # equivalent: simply rely on ISO timestamps where larger == newer.
    # The outer sort uses ``-score`` (higher first) then this as the
    # secondary key; we want newest first, so invert by returning a
    # value whose natural ascending order matches "newest first".
    return _invert_iso(row.created_at or "")


def _invert_iso(ts: str) -> str:
    # Cheap inversion: prefix-pad and invert digits so larger ISO ts
    # sorts first under ascending sort. Avoids extra datetime parsing.
    if not ts:
        return "~"  # sort last
    return "".join(_INV.get(ch, ch) for ch in ts)


_INV = {str(d): str(9 - d) for d in range(10)}


__all__ = ["retrieve_prior_runs"]
