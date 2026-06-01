"""ObsCo service layer.

Pure-Python, self-contained Q&A handler for the Observability Copilot.

- Always answers from the built-in knowledge bases (deterministic, offline).
- Knows about both external observability tools (`knowledge.py`) AND
  the Observability Studio platform itself (`studio_knowledge.py`).
- If an Anthropic API key is provided, optionally enriches the answer
  with an LLM response grounded in the same facts.
- Never imports from any other accelerator or adapter.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from accelerators.obsco.knowledge import TOOL_FACTS, detect_tools, get_facts
from accelerators.obsco.studio_knowledge import (
    STUDIO_FACTS,
    StudioMatch,
    detect_studio_topics,
    get_studio_facts,
)

logger = logging.getLogger(__name__)

# Default Claude model — kept consistent with the rest of the platform.
_DEFAULT_MODEL = "claude-sonnet-4-5"


# ──────────────────────────────────────────────────────────────────────── #
# Tool-fact section (external observability tools)
# ──────────────────────────────────────────────────────────────────────── #
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


# ──────────────────────────────────────────────────────────────────────── #
# Studio-fact section (the platform itself)
# ──────────────────────────────────────────────────────────────────────── #
# When the user asks a generic studio question with no topic keywords,
# we present this default set so the answer stays focused.
_DEFAULT_STUDIO_TOPICS: tuple[str, ...] = (
    "purpose", "how_it_works", "outputs",
)

# Map topic key → (heading, fact key, formatter).
_TOPIC_RENDERERS: dict[str, tuple[str, str]] = {
    "purpose":         ("Purpose",          "purpose"),
    "how_it_works":    ("How it works",     "how_it_works"),
    "inputs":          ("Inputs",           "inputs"),
    "outputs":         ("Outputs",          "outputs"),
    "api":             ("API endpoints",    "api_endpoints"),
    "scoring":         ("Scoring details",  "how_it_works"),
    "intent_types":    ("Intents & planning", "how_it_works"),
    "troubleshooting": ("Common issues",    "faqs"),
    "configuration":   ("Configuration",    "key_features"),
    "integrations":    ("Integrations",     "key_features"),
    "history":         ("Run history",      "outputs"),
    "faq":             ("FAQ",              "faqs"),
}


def _format_studio_section(key: str, facts: dict, topics: list[str]) -> str:
    """Render one accelerator's Studio facts, filtered by topic tags."""
    selected = list(topics) if topics else list(_DEFAULT_STUDIO_TOPICS)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for t in selected:
        if t in _TOPIC_RENDERERS and t not in seen:
            seen.add(t)
            ordered.append(t)

    lines: list[str] = [
        f"### {facts['display_name']} — *{facts.get('category', '')}*",
        "",
        facts.get("purpose", ""),
    ]
    for topic in ordered:
        if topic == "purpose":
            continue  # already shown above
        heading, fact_key = _TOPIC_RENDERERS[topic]
        value = facts.get(fact_key)
        rendered = _render_topic_value(heading, value)
        if rendered:
            lines.append("")
            lines.append(rendered)

    return "\n".join(line for line in lines if line is not None).strip()


def _render_topic_value(heading: str, value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return f"**{heading}:** {value}"
    if isinstance(value, (list, tuple)):
        # FAQ-style list of {q, a}
        if value and isinstance(value[0], dict) and "q" in value[0]:
            lines = [f"**{heading}:**"]
            for item in value:
                q = item.get("q", "").strip()
                a = item.get("a", "").strip()
                if q and a:
                    lines.append(f"- *{q}* — {a}")
            return "\n".join(lines) if len(lines) > 1 else ""
        # Generic bullet list of strings
        lines = [f"**{heading}:**"]
        for item in value:
            if isinstance(item, str) and item.strip():
                lines.append(f"- {item}")
        return "\n".join(lines) if len(lines) > 1 else ""
    return ""


def _collect_studio_facts(matches: list[StudioMatch]) -> dict[str, dict]:
    """Expand each match into the underlying fact dict (deterministic order)."""
    out: dict[str, dict] = {}
    for m in matches:
        facts = get_studio_facts(m["accelerator"])
        if facts:
            out[m["accelerator"]] = facts
    return out


# ──────────────────────────────────────────────────────────────────────── #
# Overall composition
# ──────────────────────────────────────────────────────────────────────── #
def _compose_local_answer(
    message: str,
    mentioned_tools: list[str],
    configured_tools: list[str],
    studio_matches: list[StudioMatch],
) -> tuple[str, dict[str, dict], dict[str, dict]]:
    """Build a deterministic answer from both knowledge bases.

    Returns: (markdown_answer, tool_facts_dict, studio_facts_dict).
    """
    # ── Resolve external-tool focus ──
    tool_focus = mentioned_tools or (
        configured_tools if not studio_matches else []
    )
    tool_facts: dict[str, dict] = {}
    tool_sections: list[str] = []
    for tool in tool_focus:
        facts = get_facts(tool)
        if not facts:
            continue
        tool_facts[tool] = facts
        tool_sections.append(_format_facts_section(tool, facts))

    # ── Resolve Studio focus ──
    studio_facts = _collect_studio_facts(studio_matches)
    topic_lookup = {m["accelerator"]: m["topics"] for m in studio_matches}
    studio_sections: list[str] = []
    for key, facts in studio_facts.items():
        rendered = _format_studio_section(key, facts, topic_lookup.get(key, []))
        if rendered:
            studio_sections.append(rendered)

    # ── Nothing matched — friendly fallback ──
    if not tool_sections and not studio_sections:
        return _no_match_answer(), {}, {}

    # ── Compose intro + body ──
    intro_parts: list[str] = []
    if studio_sections:
        names = ", ".join(studio_facts[k]["display_name"] for k in studio_facts)
        intro_parts.append(
            f"Here's what I know about **{names}** in Observability Studio:"
        )
    if tool_sections:
        names = ", ".join(tool_facts[t]["display_name"] for t in tool_facts)
        if intro_parts:
            intro_parts.append(
                f"And here are the relevant facts for **{names}**:"
            )
        else:
            intro_parts.append(
                f"Here's what I know about **{names}** based on your question:"
            )

    body_sections = studio_sections + tool_sections
    answer = "\n\n".join(intro_parts) + "\n\n" + "\n\n---\n\n".join(body_sections)
    return answer, tool_facts, studio_facts


def _no_match_answer() -> str:
    studio_names = ", ".join(
        f["display_name"]
        for k, f in STUDIO_FACTS.items()
        if k not in ("platform", "obsco")
    )
    tool_names = ", ".join(sorted(TOOL_FACTS.keys()))
    return (
        "I'm **ObsCo** — your Observability Copilot. I can answer questions about:\n\n"
        f"- **Observability Studio accelerators**: {studio_names}\n"
        f"- **Connected observability tools**: {tool_names}\n\n"
        "Try asking *'How does ObservaScore work?'*, *'What does AYOSA "
        "output?'*, or *'How do I query errors in Splunk?'*"
    )


# ──────────────────────────────────────────────────────────────────────── #
# Optional LLM enhancement
# ──────────────────────────────────────────────────────────────────────── #
def _build_grounding(
    tool_facts: dict[str, dict],
    studio_facts: dict[str, dict],
    topic_lookup: dict[str, list[str]],
) -> str:
    parts: list[str] = []
    if studio_facts:
        parts.append("## Observability Studio platform facts\n")
        for key, facts in studio_facts.items():
            parts.append(_format_studio_section(
                key, facts, topic_lookup.get(key, []),
            ))
    if tool_facts:
        parts.append("## External tool facts\n")
        for tool, facts in tool_facts.items():
            parts.append(_format_facts_section(tool, facts))
    return "\n\n".join(parts)


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
            "You are ObsCo, the Observability Copilot for the Observability "
            "Studio SRE platform. Answer the user's question concisely and "
            "accurately using ONLY the provided platform and tool facts. "
            "Use Markdown. If the facts don't cover the question, say so "
            "plainly — do not invent endpoints, queries, file paths, "
            "scoring rules, or behaviours."
        )
        user_prompt = (
            f"User question:\n{message}\n\n"
            f"Authoritative facts (do not contradict):\n{grounding}"
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


# ──────────────────────────────────────────────────────────────────────── #
# Public entry point
# ──────────────────────────────────────────────────────────────────────── #
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
    studio_matches = detect_studio_topics(message)

    local_answer, tool_facts, studio_facts = _compose_local_answer(
        message, mentioned, configured, studio_matches,
    )

    ai_used = False
    answer = local_answer

    ai = getattr(req, "ai", None)
    if (
        ai
        and getattr(ai, "enabled", False)
        and getattr(ai, "api_key", None)
        and (tool_facts or studio_facts)
    ):
        topic_lookup = {m["accelerator"]: m["topics"] for m in studio_matches}
        grounding = _build_grounding(tool_facts, studio_facts, topic_lookup)
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
        "mentioned_accelerators": [m["accelerator"] for m in studio_matches],
        "studio_facts": studio_facts,
        "ai_used": ai_used,
    }
