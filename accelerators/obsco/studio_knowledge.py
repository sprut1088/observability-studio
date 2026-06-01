"""Built-in knowledge base for the **Observability Studio platform itself**.

Self-contained, pure-Python. No external imports. Sits next to
`knowledge.py` (which describes external observability tools) so that
ObsCo can answer questions about both layers from one chat surface.

Public API:
    STUDIO_FACTS           : registry keyed by accelerator id
    STUDIO_TOPICS          : intent vocabulary
    detect_studio_topics(text) -> list[StudioMatch]
    get_studio_facts(key)  -> dict | None
    list_studio_keys()     -> list[str]

A `StudioMatch` is a small dict:
    {
        "accelerator": "observascore",
        "topics":      ["how_it_works", "outputs"],
    }

The knowledge here is curated, conservative, and version-stable. Adding
new accelerators is one append per entry — no other code needs to know.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict


# ──────────────────────────────────────────────────────────────────────── #
# Intent / topic vocabulary
# ──────────────────────────────────────────────────────────────────────── #
class StudioMatch(TypedDict):
    accelerator: str
    topics: list[str]


# Each topic maps to a list of single-word regex tokens. Detection
# considers a topic matched when at least one of its tokens appears in
# the normalised query text.
STUDIO_TOPICS: dict[str, tuple[str, ...]] = {
    "purpose":         ("what", "purpose", "about", "do", "does"),
    "how_it_works":    ("how", "work", "works", "working", "process",
                        "pipeline", "algorithm", "logic", "explain"),
    "inputs":          ("input", "inputs", "require", "requires", "needs",
                        "parameter", "parameters", "payload"),
    "outputs":         ("output", "outputs", "result", "results", "report",
                        "artifact", "artifacts", "export", "download",
                        "produce", "produces", "generate", "generates"),
    "api":             ("api", "endpoint", "endpoints", "route", "routes",
                        "url", "post", "get"),
    "scoring":         ("score", "scoring", "rule", "rules", "dimension",
                        "dimensions", "weight", "weighting"),
    "intent_types":    ("intent", "intents", "classify", "classifier",
                        "category", "categories"),
    "troubleshooting": ("error", "errors", "fail", "fails", "failed",
                        "failing", "broken", "issue", "issues", "debug",
                        "troubleshoot", "fix"),
    "configuration":   ("config", "configure", "configuration", "setup",
                        "install", "feature", "flag", "flags", "env",
                        "environment"),
    "integrations":    ("integrate", "integration", "integrations",
                        "connect", "connection", "client", "mcp"),
    "history":         ("history", "compare", "comparison", "diff", "run",
                        "runs", "persist", "persistence", "session",
                        "sessions"),
    "faq":             ("why", "when", "should", "best", "practice",
                        "limitation", "limitations"),
}


# ──────────────────────────────────────────────────────────────────────── #
# Per-accelerator fact registry
# ──────────────────────────────────────────────────────────────────────── #
STUDIO_FACTS: dict[str, dict[str, Any]] = {
    "platform": {
        "display_name": "Observability Studio",
        "category": "platform",
        "purpose": (
            "A multi-accelerator SRE platform that crawls observability "
            "tools, scores their maturity, performs AI-driven root cause "
            "analysis, and answers live questions about your stack."
        ),
        "inputs": [
            "Connection details for one or more observability tools "
            "(URL, optional bearer/API token).",
            "Optional Anthropic API key for AI enrichment (never stored).",
        ],
        "how_it_works": [
            "React Hub UI sends a request to a FastAPI route under /api/v1/*.",
            "A thin route handler delegates to a service in backend/app/services/.",
            "Services call accelerator logic in accelerators/<name>/.",
            "Adapters perform read-only HTTP calls to the source tools.",
            "Generated artifacts are written under runtime/<run_id>/.",
            "Responses include an absolute download_url for HTML/XLSX/JSON.",
        ],
        "outputs": [
            "HTML reports, JSON reports, and XLSX workbooks under runtime/.",
        ],
        "api_endpoints": [
            "GET  /api/health",
            "GET  /api/feature-flags",
            "POST /api/v1/validate",
            "POST /api/v1/crawl",
            "POST /api/v1/assess",
            "POST /api/v1/rca",
            "POST /api/v1/obsco/chat",
        ],
        "key_features": [
            "Six accelerator tiles plus an always-on ObsCo copilot.",
            "Feature-flag middleware gates every accelerator route.",
            "All extraction is read-only — no writes to source tools.",
            "AI enrichment is additive and optional everywhere.",
        ],
        "faqs": [
            {
                "q": "Where are generated reports stored?",
                "a": "Under `runtime/<run_id>/<module>/` and served via "
                     "`GET /api/download/runtime/{path}`.",
            },
            {
                "q": "How do I disable an accelerator?",
                "a": "Set its flag to `false` in "
                     "`studio_platform/config/feature_flags.yaml`. The middleware "
                     "in `backend/app/main.py` enforces it for both UI and API.",
            },
        ],
        "related_docs": ["README.md", ".claude/CLAUDE.md"],
        "aliases": ("observability studio", "the platform", "studio",
                    "hub", "this platform", "this app"),
    },
    "obscrawl": {
        "display_name": "ObsCrawl",
        "category": "crawl_and_export",
        "purpose": (
            "Connect to any supported observability tool, validate the "
            "endpoint, then extract its full telemetry estate (services, "
            "dashboards, alerts, metrics, datasources, indexes, …) and "
            "deliver it as a structured multi-sheet Excel workbook."
        ),
        "inputs": [
            "tool_name (e.g. 'prometheus', 'grafana', 'splunk').",
            "base_url (root URL of the tool).",
            "Optional auth_token (Bearer or API key).",
        ],
        "how_it_works": [
            "POST /api/v1/validate first probes the tool's health endpoint.",
            "POST /api/v1/crawl spawns the observascore CLI in a subprocess.",
            "The matching adapter performs read-only HTTP GETs to enumerate "
            "the estate.",
            "Results are normalised into the Common Observability Model (COM).",
            "openpyxl writes a multi-sheet XLSX under runtime/<run_id>/exports/.",
        ],
        "outputs": [
            "A multi-sheet Excel workbook (.xlsx) with one sheet per entity "
            "type (Services, Dashboards, Alerts, Metrics, Datasources, …).",
            "Response payload includes an absolute `download_url`.",
        ],
        "api_endpoints": [
            "POST /api/v1/validate",
            "POST /api/v1/crawl",
        ],
        "key_features": [
            "Tool connectivity probe before extraction.",
            "Adapter-driven extraction across 12+ tools.",
            "Read-only — never writes to the source tool.",
            "TLS verification is disabled by design for lab self-signed certs.",
        ],
        "faqs": [
            {
                "q": "Why is my extraction empty?",
                "a": "Check that `/api/v1/validate` reports `reachable: true` "
                     "first — usually the base_url is missing a port or the "
                     "auth_token doesn't have read permissions.",
            },
            {
                "q": "Can I crawl multiple tools at once?",
                "a": "Yes — use the legacy `POST /api/export` route for "
                     "multi-tool extraction. New work should target "
                     "`/api/v1/crawl` per tool.",
            },
        ],
        "related_docs": [".claude/rules/api.md"],
        "aliases": ("obscrawl", "obs crawl", "obs-crawl",
                    "the crawler", "crawler"),
    },
    "observascore": {
        "display_name": "ObservaScore",
        "category": "assess_and_score",
        "purpose": (
            "Run a deterministic maturity assessment against your "
            "observability tool stack. Applies 35+ rules across 10 "
            "dimensions to score coverage and readiness, with optional "
            "AI-powered gap analysis."
        ),
        "inputs": [
            "tool_source (which tool to assess, e.g. 'prometheus').",
            "api_endpoint and optional auth_token.",
            "Optional use_ai + ai_provider + ai_api_key for narrative gap analysis.",
        ],
        "how_it_works": [
            "Adapter extracts the tool's estate into the COM dataclasses.",
            "engine/scoring.py walks each rule pack in accelerators/observascore/rules/.",
            "Each rule emits a deterministic score on a 100-point scale.",
            "Scores roll up by dimension and overall — no AI is required.",
            "When AI is enabled, ai/analyst.py asks Claude (or Azure OpenAI) "
            "to write a grounded narrative for each gap.",
            "report/generator.py renders Jinja2 HTML + JSON; "
            "export/excel.py writes a workbook.",
        ],
        "outputs": [
            "An HTML report (primary download).",
            "A structured JSON report.",
            "A multi-sheet XLSX workbook with raw scores + evidence.",
        ],
        "api_endpoints": [
            "POST /api/v1/assess",
            "POST /api/assess  (legacy alias)",
        ],
        "key_features": [
            "35+ scoring rules across 10 maturity dimensions.",
            "Deterministic baseline; AI enrichment is optional.",
            "Common Observability Model (COM) normalises every tool.",
            "Standalone CLI: `python -m observascore.cli assess|export|check`.",
        ],
        "faqs": [
            {
                "q": "Why are my scores all zero?",
                "a": "Almost always an extraction problem — run "
                     "`python -m observascore.cli check --config ...` to see "
                     "which adapter calls came back empty.",
            },
            {
                "q": "Can I add my own rule?",
                "a": "Yes. Add a YAML rule pack under "
                     "`accelerators/observascore/rules/` and a Python check "
                     "function. The engine auto-discovers both.",
            },
        ],
        "related_docs": [".claude/rules/backend.md"],
        "aliases": ("observascore", "observa score", "observa-score",
                    "maturity score", "maturity assessment"),
    },
    "rca_agent": {
        "display_name": "RCA Agent",
        "category": "analyse_and_investigate",
        "purpose": (
            "Multi-tool root cause analysis. Collects signals from "
            "Prometheus, Grafana, Jaeger, and OpenSearch during an "
            "incident, correlates anomalies, walks the service graph to "
            "compute blast radius, and renders a Claude-powered RCA report."
        ),
        "inputs": [
            "tools[]: list of {tool_name, base_url, optional auth_token}.",
            "incident: {service, alert_name, description, time_window_minutes}.",
            "ai_api_key + ai_model (Anthropic Claude).",
        ],
        "how_it_works": [
            "SignalCollector pulls metrics, alerts, traces, and logs for the window.",
            "CorrelationEngine ranks anomalies via thresholds.yaml + correlation_rules.yaml.",
            "CascadeDetector runs BFS through the service graph for blast radius.",
            "LLMFormatter asks Claude for a structured RCA JSON.",
            "Jinja2 renders the JSON into a self-contained HTML report.",
        ],
        "outputs": [
            "An HTML RCA report under runtime/<run_id>/rca/.",
            "Top-line counts in the response: anomaly_count, "
            "firing_alert_count, error_log_count, blast_radius.",
        ],
        "api_endpoints": ["POST /api/v1/rca"],
        "key_features": [
            "Runs inline (no subprocess) — fast turnaround.",
            "Service-graph BFS computes blast radius across dependencies.",
            "Thresholds and correlation rules live in YAML — easy to tune.",
        ],
        "faqs": [
            {
                "q": "Which signals are required?",
                "a": "At least one of metrics or alerts is enough to start. "
                     "Traces and logs improve accuracy of root cause and "
                     "blast radius.",
            },
        ],
        "related_docs": ["accelerators/rca-agent/README.md"],
        "aliases": ("rca", "rca agent", "root cause", "root-cause",
                    "root cause analysis", "incident analysis"),
    },
    "red_panel_intelligence": {
        "display_name": "RED Panel Intelligence",
        "category": "dashboard_red_coverage",
        "purpose": (
            "Service-centric Rate / Errors / Duration dashboard coverage "
            "scoring across Grafana, Splunk, Datadog, Dynatrace, and "
            "AppDynamics. Detects weak panels, missing queries, and "
            "incomplete service views."
        ),
        "inputs": [
            "Configured dashboard tool(s) to analyse.",
            "Optional service scope.",
        ],
        "how_it_works": [
            "Walks every dashboard returned by the tool's adapter.",
            "Classifies each panel as Rate, Errors, or Duration via "
            "title + query heuristics.",
            "Scores per-service RED coverage and surfaces gaps.",
        ],
        "outputs": [
            "An HTML report with per-service RED matrix.",
            "JSON with panel-level classification.",
        ],
        "api_endpoints": ["POST /api/red-intelligence"],
        "key_features": [
            "Multi-tool dashboard analysis from one request.",
            "Deterministic panel classification — no AI required.",
        ],
        "faqs": [],
        "related_docs": [],
        "aliases": ("red", "red panel", "red intelligence",
                    "red coverage", "dashboard coverage"),
    },
    "observability_gap_map": {
        "display_name": "Observability Gap Map",
        "category": "application_service_coverage",
        "purpose": (
            "Per-application service coverage map across metrics, logs, "
            "traces, dashboards, alerts, and RED readiness — plus a "
            "separate Signal Connectivity layer that checks debugging-path "
            "links between signals."
        ),
        "inputs": [
            "Application scope plus configured tool(s).",
        ],
        "how_it_works": [
            "Coverage layer scores each service against six signal types.",
            "Signal Connectivity layer runs deterministic checks: "
            "metrics_to_logs, logs_to_traces, alerts_to_dashboards, "
            "dashboards_to_logs, dashboards_to_traces.",
            "Each check returns PASS=100, WARN=60, or FAIL=0.",
            "MTTR risk = low (≥80), medium (50-79), high (<50).",
        ],
        "outputs": [
            "HTML report with coverage matrix + connectivity section.",
            "JSON fields: coverage matrix, `connectivity_results`, "
            "`connectivity_summary`.",
        ],
        "api_endpoints": ["POST /api/observability-gap-map"],
        "key_features": [
            "Application-scoped service inventory.",
            "Interactive signal coverage matrix.",
            "Auto-discovery suggestions with noise filtering.",
        ],
        "faqs": [],
        "related_docs": [],
        "aliases": ("gap map", "gap-map", "observability gap",
                    "coverage map", "blind spot"),
    },
    "ayosa": {
        "display_name": "AYOSA",
        "category": "ask_your_observability_stack_anything",
        "purpose": (
            "Chat-driven investigation copilot. Pulls live signals from "
            "your connected tools, classifies intent, plans tool queries, "
            "and returns evidence, charts, RCA snapshots, and a synthesized "
            "answer — with optional Claude narrative."
        ),
        "inputs": [
            "message (free text).",
            "tools[]: list of {tool, base_url, optional auth_token}.",
            "Optional service, time_range, session_id, agent_mode flag, ai config.",
        ],
        "how_it_works": [
            "intent_classifier maps the message to one of 11 intents.",
            "planner consults a 12-tool registry and the workspace index "
            "to pick required signals and selected tools.",
            "tool_dispatcher invokes adapters in parallel where safe.",
            "synthesizer composes evidence, timeline, charts, and an answer.",
            "Optional LLM step grounds a narrative in the collected evidence.",
            "Every agent-mode run is persisted best-effort to "
            "runtime/ayosa.db for later comparison.",
        ],
        "outputs": [
            "JSON chat response: answer, evidence, timeline, "
            "incident_snapshot, signal_coverage, missing_signals, "
            "tool_steps, plan, workspace_context, session_id, run_id.",
            "SSE stream of events when /api/ayosa/chat/stream is used.",
        ],
        "api_endpoints": [
            "POST /api/ayosa/chat",
            "POST /api/ayosa/chat/stream",
            "POST /api/ayosa/runbook",
            "GET  /api/ayosa/runs",
            "GET  /api/ayosa/runs/{run_id}",
            "GET  /api/ayosa/runs/compare?left=&right=",
        ],
        "key_features": [
            "11 intent types with registry-driven tool selection.",
            "Workspace awareness — never invents services or dashboards.",
            "Session memory infers blank service/time_range from prior turns.",
            "Persists every run to SQLite for history and comparison.",
            "SSE event vocabulary: session_start, intent, plan, "
            "tool_start, tool_result, observation, chart, "
            "timeline_event, llm_chunk, final_snapshot, done, error.",
        ],
        "faqs": [
            {
                "q": "What's the difference between deterministic and agent mode?",
                "a": "Deterministic mode (`agent_mode=false`) runs the "
                     "legacy AyosaService for a fixed pipeline. Agent mode "
                     "(`agent_mode=true`) uses the planner/dispatcher/"
                     "synthesizer loop with session memory and workspace "
                     "context.",
            },
            {
                "q": "How do I compare two investigations?",
                "a": "GET /api/ayosa/runs/compare?left=<id>&right=<id> "
                     "returns a field-level diff of intent, service, "
                     "tools_used, confidence, answer, and snapshot subkeys.",
            },
            {
                "q": "What does 'workspace index unavailable' mean?",
                "a": "AYOSA found no indexed ObsCrawl / ObservaScore "
                     "artifacts. Run a crawl first and call "
                     "`index_workspace(run_id, artifact_path)` to populate "
                     "`runtime/workspace_index/`.",
            },
        ],
        "related_docs": ["accelerators/ayosa/README.md"],
        "aliases": ("ayosa", "ask your observability", "investigation copilot",
                    "the agent", "ayosa agent"),
    },
    "obsco": {
        "display_name": "ObsCo",
        "category": "genai_copilot",
        "purpose": (
            "Always-on floating chat copilot. Answers questions about your "
            "connected observability tools AND about Observability Studio "
            "itself, grounded in a built-in knowledge base. Optionally "
            "enhanced by Claude."
        ),
        "inputs": [
            "message (free text).",
            "tools[]: optional list of configured tools to scope the answer.",
            "Optional ai config (Anthropic API key).",
        ],
        "how_it_works": [
            "Detects which external tools the question mentions.",
            "Detects which Observability Studio accelerators the question is about.",
            "Composes a deterministic answer from the knowledge base.",
            "If an API key is supplied, asks Claude to rewrite the answer "
            "grounded in the same facts.",
        ],
        "outputs": [
            "JSON response: answer, mentioned_tools, configured_tools, "
            "tool_facts, mentioned_accelerators, studio_facts, ai_used.",
        ],
        "api_endpoints": ["POST /api/v1/obsco/chat"],
        "key_features": [
            "Self-contained — no other accelerator imports.",
            "Works fully offline (deterministic answer).",
            "Knows both the external tools and the Studio platform.",
        ],
        "faqs": [
            {
                "q": "Does ObsCo store my Anthropic API key?",
                "a": "No. Keys are passed per-request and never persisted.",
            },
        ],
        "related_docs": [],
        "aliases": ("obsco", "copilot", "the copilot", "this chatbot",
                    "you"),
    },
    "mcp_server": {
        "display_name": "MCP Server (experimental)",
        "category": "integrations",
        "purpose": (
            "Optional Model Context Protocol stdio server that exposes "
            "AYOSA tools to MCP-aware clients such as Claude Desktop, "
            "VS Code, and Cursor."
        ),
        "inputs": [
            "Per-tool: base_url, optional auth_token, optional service / "
            "time_range / message.",
        ],
        "how_it_works": [
            "Reuses the existing accelerators/ayosa/registry.py adapters.",
            "Runs over stdio; no network listener.",
            "All auth_token / api_key / secret fields are redacted before logging.",
            "If the optional `mcp` package is missing, exits with code 2 "
            "and prints an install hint.",
        ],
        "outputs": [
            "MCP tool results as JSON text content blocks.",
        ],
        "api_endpoints": [],
        "key_features": [
            "8 tools: query_prometheus, query_elasticsearch, query_splunk, "
            "query_alertmanager, query_jaeger, ayosa_investigate, "
            "ayosa_search_workspace, ayosa_get_run.",
            "Optional dependency — never affects the rest of the platform.",
            "Sensitive fields flagged in JSON Schema and scrubbed in logs.",
        ],
        "faqs": [
            {
                "q": "How do I run it?",
                "a": "`python -m mcp_server.server` from the repo root. See "
                     "`mcp_server/README.md` for client wiring.",
            },
        ],
        "related_docs": ["mcp_server/README.md"],
        "aliases": ("mcp", "mcp server", "model context protocol",
                    "claude desktop"),
    },
}


# Frozen at import time so detection is allocation-free per call.
_ALIAS_INDEX: tuple[tuple[str, str], ...] = tuple(
    (alias.lower(), key)
    for key, facts in STUDIO_FACTS.items()
    for alias in (facts.get("aliases") or ())
)


# Tokeniser used both for accelerator detection and topic detection.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


# ──────────────────────────────────────────────────────────────────────── #
# Public API
# ──────────────────────────────────────────────────────────────────────── #
def list_studio_keys() -> list[str]:
    return list(STUDIO_FACTS.keys())


def get_studio_facts(key: str) -> dict[str, Any] | None:
    return STUDIO_FACTS.get((key or "").lower().strip())


def detect_studio_topics(text: str) -> list[StudioMatch]:
    """Return matched accelerators + topic tags for one query.

    Detection is deterministic and conservative:

    1. An accelerator matches when one of its aliases appears as a
       whitespace-bounded substring of the normalised text. (The
       'platform' entry matches its own aliases — never as a fallback,
       so a tool-only question still returns []).
    2. Topics are matched against a stable keyword vocabulary
       (`STUDIO_TOPICS`).
    3. Returns at most one entry per accelerator, preserving insertion
       order of STUDIO_FACTS for deterministic output.

    The function never raises; it returns `[]` for empty input.
    """
    if not text:
        return []
    normalised = f" {text.lower()} "

    # Collect matched accelerators with priority to longer aliases so
    # that, e.g., "rca agent" beats "rca" for a single tag.
    matched: dict[str, None] = {}
    for alias, key in sorted(_ALIAS_INDEX, key=lambda x: -len(x[0])):
        if f" {alias} " in normalised or _has_word_boundary(normalised, alias):
            matched.setdefault(key, None)

    if not matched:
        return []

    tokens = {m.lower() for m in _TOKEN_RE.findall(text)}
    topics = [
        topic
        for topic, kws in STUDIO_TOPICS.items()
        if any(kw in tokens for kw in kws)
    ]

    # Preserve STUDIO_FACTS declaration order in the output.
    return [
        StudioMatch(accelerator=key, topics=list(topics))
        for key in STUDIO_FACTS
        if key in matched
    ]


# ──────────────────────────────────────────────────────────────────────── #
# Internals
# ──────────────────────────────────────────────────────────────────────── #
def _has_word_boundary(haystack: str, needle: str) -> bool:
    """Match `needle` only at word boundaries inside `haystack`.

    `haystack` is assumed to be lowercased and bracketed by spaces.
    Cheaper than a regex compile per call, and avoids the false-positive
    of "score" matching "scoring" mid-token.
    """
    if " " in needle:
        return False  # already covered by the substring check
    idx = 0
    while True:
        idx = haystack.find(needle, idx)
        if idx == -1:
            return False
        before = haystack[idx - 1] if idx > 0 else " "
        after = haystack[idx + len(needle)] if idx + len(needle) < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        idx += 1


__all__ = [
    "STUDIO_FACTS",
    "STUDIO_TOPICS",
    "StudioMatch",
    "detect_studio_topics",
    "get_studio_facts",
    "list_studio_keys",
]
