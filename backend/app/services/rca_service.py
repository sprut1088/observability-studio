"""
rca_service.py
───────────────
Backend service that drives the RCA Agent for the /api/v1/rca endpoint.

Design:
  - Runs the RCA Agent inline (no subprocess) so we get structured results.
  - Adds the rca-agent/src directory to sys.path so the agent modules resolve.
  - Writes the HTML report to runtime/<run_id>/rca/ and returns a download URL.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO_ROOT   = Path(__file__).resolve().parents[3]
_RCA_SRC     = _REPO_ROOT / "accelerators" / "rca-agent" / "src"
RUNTIME_DIR  = _REPO_ROOT / "runtime"
BASE_URL     = "http://10.235.21.132:8001"

# Ensure the RCA agent source is importable
if str(_RCA_SRC) not in sys.path:
    sys.path.insert(0, str(_RCA_SRC))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def run_rca(request_data: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a full RCA analysis and return a structured response dict.

    Args:
        request_data: validated RCARequest (or dict) with keys:
            tools              – list of {tool_name, base_url, auth_token}
            incident           – {service, alert_name, description, time_window_minutes}
            ai_provider        – "anthropic" (default) | "azure"
            ai_api_key         – API key (Anthropic or Azure)
            ai_model           – Anthropic model name
            azure_endpoint     – Azure OpenAI endpoint URL
            azure_deployment   – Azure deployment name
            azure_api_version  – Azure API version
    """
    tools    = [t.model_dump() if hasattr(t, "model_dump") else dict(t)
                for t in request_data.get("tools", [])]
    incident_obj = request_data.get("incident", {})
    incident = (incident_obj.model_dump() if hasattr(incident_obj, "model_dump")
                else dict(incident_obj))

    provider = (request_data.get("ai_provider") or "anthropic").lower()
    api_key  = request_data.get("ai_api_key") or None
    model    = request_data.get("ai_model") or "claude-sonnet-4-6"

    # Build the unified ai_config dict that LLMFormatter expects
    ai_config: dict[str, Any] = {"provider": provider, "api_key": api_key or ""}
    if provider == "anthropic":
        ai_config["model"] = model
    else:  # azure
        ai_config["azure_endpoint"]    = request_data.get("azure_endpoint") or ""
        ai_config["azure_deployment"]  = request_data.get("azure_deployment") or "gpt-4o"
        ai_config["azure_api_version"] = request_data.get("azure_api_version") or "2024-02-01"

    result = await asyncio.to_thread(
        _run_agent_sync, tools, incident, ai_config
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Sync implementation (runs in thread pool via asyncio.to_thread)
# ─────────────────────────────────────────────────────────────────────────────

def _run_agent_sync(
    tools: list[dict],
    incident: dict,
    ai_config: dict[str, Any],
) -> dict[str, Any]:
    try:
        from rca_agent import RCAAgent
    except ImportError as exc:
        logger.error("Cannot import RCAAgent: %s — ensure %s is on sys.path", exc, _RCA_SRC)
        raise RuntimeError(f"RCA Agent import failed: {exc}") from exc

    agent = RCAAgent(tools=tools, ai_config=ai_config)

    output_dir = RUNTIME_DIR  # agent will create run_id subdir internally
    result = agent.run(incident=incident, output_dir=None)

    run_id    = result["run_id"]
    html_path = result["html_path"]

    # Build the download URL relative to RUNTIME_DIR
    try:
        rel = html_path.relative_to(RUNTIME_DIR)
        download_url = f"{BASE_URL}/api/download/runtime/{rel.as_posix()}"
    except ValueError:
        download_url = f"{BASE_URL}/api/download/runtime/{run_id}/rca/{html_path.name}"

    correlation = result.get("correlation")
    signals     = result.get("signals")
    cascade     = result.get("cascade", {})

    return {
        "success":            result["success"],
        "message":            _build_message(result),
        "download_url":       download_url,
        "run_id":             run_id,
        "anomaly_count":      len(correlation.anomalies) if correlation else 0,
        "firing_alert_count": correlation.total_firing_alerts if correlation else 0,
        "error_log_count":    correlation.total_error_logs if correlation else 0,
        "blast_radius":       cascade.get("blast_radius", 0),
    }


def _build_message(result: dict[str, Any]) -> str:
    corr    = result.get("correlation")
    cascade = result.get("cascade", {})
    ai_on   = result.get("rca_data", {}).get("ai_powered", False)

    parts = []
    if corr:
        parts.append(f"{len(corr.anomalies)} anomaly(ies) detected")
        if corr.total_firing_alerts:
            parts.append(f"{corr.total_firing_alerts} firing alert(s)")
        if corr.total_error_logs:
            parts.append(f"{corr.total_error_logs} error log(s)")
    if cascade.get("blast_radius"):
        parts.append(f"blast radius {cascade['blast_radius']} service(s)")
    if result.get("error"):
        parts.append(f"warning: {result['error']}")

    summary = ", ".join(parts) if parts else "Analysis complete"
    ai_note = " (AI-powered)" if ai_on else " (deterministic)"
    return f"RCA complete{ai_note} — {summary}"
