"""Step 12(a) shared helper — extract the LLM-emitted ``query`` and
``reason`` from the plan dict carried by ``ToolDispatcher`` under
``plan['active_tool_args']``.

Kept as a tiny pure module so each plan-accepting adapter can use it
without depending on Splunk's local copy.
"""

from __future__ import annotations


def extract_llm_query(plan: dict | None) -> str | None:
    """Return the trimmed LLM ``query`` string or ``None`` when absent."""
    if not isinstance(plan, dict):
        return None
    ta = plan.get("active_tool_args")
    if not isinstance(ta, dict):
        return None
    q = ta.get("query")
    if isinstance(q, str) and q.strip():
        return q.strip()
    return None


def extract_llm_reason(plan: dict | None) -> str:
    """Return the LLM ``reason`` or an empty string."""
    if not isinstance(plan, dict):
        return ""
    ta = plan.get("active_tool_args")
    if not isinstance(ta, dict):
        return ""
    r = ta.get("reason")
    return r.strip() if isinstance(r, str) else ""


__all__ = ["extract_llm_query", "extract_llm_reason"]
