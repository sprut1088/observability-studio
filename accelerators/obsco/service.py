"""ObsCo service layer.

Pure-Python, self-contained Q&A handler for the Observability Copilot.

- Always answers from the built-in knowledge base (deterministic, offline).
- If an Anthropic API key is provided, optionally enriches the answer with an
  LLM-generated response grounded in the same tool facts.
- Never imports from any other accelerator or adapter.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from accelerators.obsco.knowledge import TOOL_FACTS, detect_tools, get_facts

logger = logging.getLogger(__name__)

# Default Claude model — kept consistent with the rest of the platform.
_DEFAULT_MODEL = "claude-sonnet-4-5"


def _format_facts_section(tool: str, facts: dict) -> str:
    lines = [f"### {facts['display_name']} ({facts['category']})", ""]
    lines.append(facts["purpose"])
    if facts.get("common_endpoints"):
        lines.append("\n**Common endpoints:**")
        lines.extend(f"- `{e}`" for e in facts["common_endpoints"])
    if facts.get("auth_methods"):
        lines.append("\n**Auth methods:** " + ", ".join(facts["auth_methods"]))
    if facts.get("common_queries"):
        lines.append("\n**Common queries:**")
        lines.extend(f"- `{q}`" for q in facts["common_queries"])
    if facts.get("troubleshooting_tips"):
        lines.append("\n**Tips:**")
        lines.extend(f"- {t}" for t in facts["troubleshooting_tips"])
    if facts.get("useful_links"):
        lines.append("\n**Docs:**")
        lines.extend(f"- {u}" for u in facts["useful_links"])
    return "\n".join(lines)


def _compose_local_answer(
    message: str,
    mentioned: list[str],
    configured: list[str],
) -> tuple[str, dict[str, dict]]:
    """Build a deterministic answer from the knowledge base."""
    focus = mentioned or configured
    if not focus:
        names = ", ".join(sorted(TOOL_FACTS.keys()))
        return (
            "I'm ObsCo — your Observability Copilot. I can answer questions "
            "about any of the observability tools you've connected (or any I "
            f"know about: {names}). Try asking *'How do I query errors in "
            "Splunk?'* or *'What endpoints does Prometheus expose?'*",
            {},
        )

    tool_facts: dict[str, dict] = {}
    sections: list[str] = []
    for tool in focus:
        facts = get_facts(tool)
        if not facts:
            continue
        tool_facts[tool] = facts
        sections.append(_format_facts_section(tool, facts))

    if not sections:
        return (
            "I couldn't find any of your configured tools in that question. "
            "Try mentioning one of: " + ", ".join(sorted(TOOL_FACTS.keys())),
            {},
        )

    intro = (
        f"Here's what I know about **{', '.join(tool_facts[t]['display_name'] for t in tool_facts)}** "
        "based on your question:"
    )
    return intro + "\n\n" + "\n\n---\n\n".join(sections), tool_facts


def _try_llm_enhance(
    message: str,
    grounding: str,
    api_key: str,
    model: str | None,
) -> str | None:
    """Best-effort LLM call. Returns None on any failure."""
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        logger.info("anthropic SDK not installed; skipping LLM enhancement.")
        return None

    try:
        client = Anthropic(api_key=api_key)
        system_prompt = (
            "You are ObsCo, the Observability Copilot for an SRE platform. "
            "Answer the user's question concisely and accurately, grounding "
            "every claim in the provided tool facts. Use Markdown. If the "
            "facts don't cover the question, say so plainly — do not invent "
            "endpoints, queries or auth methods."
        )
        user_prompt = (
            f"User question:\n{message}\n\n"
            f"Authoritative tool facts (do not contradict):\n{grounding}"
        )
        resp = client.messages.create(
            model=model or _DEFAULT_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        parts = []
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip() or None
    except Exception as exc:  # noqa: BLE001 — never let LLM errors break chat
        logger.warning("ObsCo LLM enhancement failed: %s", exc)
        return None


async def answer_question(req: Any) -> dict:
    """Service entry point.

    `req` is a Pydantic `ObsCoChatRequest`. Returns a dict matching
    `ObsCoChatResponse`.
    """
    message = (req.message or "").strip()
    configured = [
        (t.tool or "").lower().strip()
        for t in (req.tools or [])
        if (t.tool or "").strip()
    ]
    mentioned = detect_tools(message)

    local_answer, tool_facts = _compose_local_answer(message, mentioned, configured)

    ai_used = False
    answer = local_answer

    ai = getattr(req, "ai", None)
    if ai and getattr(ai, "enabled", False) and getattr(ai, "api_key", None) and tool_facts:
        grounding = "\n\n".join(
            _format_facts_section(t, f) for t, f in tool_facts.items()
        )
        enhanced = await asyncio.to_thread(
            _try_llm_enhance,
            message,
            grounding,
            ai.api_key,
            getattr(ai, "model", None),
        )
        if enhanced:
            answer = enhanced
            ai_used = True

    return {
        "answer": answer,
        "mentioned_tools": mentioned,
        "configured_tools": configured,
        "tool_facts": tool_facts,
        "ai_used": ai_used,
    }
