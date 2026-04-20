"""
llm_formatter.py
─────────────────
Uses either Anthropic Claude or Azure OpenAI to synthesise signal data into a
structured, detailed RCA report.  Mirrors the provider-dispatch pattern used
by observascore/ai/analyst.py so both accelerators support the same AI backends.

Returns both a machine-readable dict and a rendered HTML string (via Jinja2).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_SYSTEM_PROMPT = """\
You are a senior Site Reliability Engineer with 15+ years of experience in
distributed systems, observability, and incident management.

You excel at:
- Rapid root-cause analysis from multi-signal observability data
- Correlating Prometheus alerts, distributed traces (Jaeger), and log patterns
- Understanding cascading failures in microservice architectures
- Writing clear, actionable RCA reports for both technical and executive audiences

When given incident signals, you ALWAYS:
1. Identify the most probable root cause with specific metric evidence
2. Explain the cascade / blast radius concisely
3. Provide a concrete, prioritised remediation plan
4. Distinguish between symptoms and actual root causes
5. Estimate customer / business impact

Respond ONLY with a valid JSON object matching the schema described in the user message.
"""


class LLMFormatterError(Exception):
    """Raised when the LLM formatter cannot be initialised."""


class LLMFormatter:
    """
    Drives Claude (Anthropic) or Azure OpenAI to produce a structured RCA JSON,
    then renders it as an HTML report via Jinja2.

    Initialisation config keys (all optional where a default exists):
      provider          – "anthropic" (default) | "azure" | "azure_openai"
      api_key           – Anthropic or Azure API key
      model             – Anthropic model name (default: claude-sonnet-4-6)
      azure_endpoint    – Required for Azure (e.g. https://my.openai.azure.com/)
      azure_deployment  – Azure deployment name (e.g. gpt-4o)
      azure_api_version – Azure API version (default: 2024-02-01)
      max_tokens        – LLM max output tokens (default: 4096)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Accepts a unified config dict.  For backwards-compat, also accepts
        the old positional-keyword signature: LLMFormatter(api_key=..., model=...)
        by converting those kwargs into a config dict.
        """
        if config is None:
            config = {}

        self.provider    = (config.get("provider") or "anthropic").strip().lower()
        self.max_tokens  = int(config.get("max_tokens", 4096))

        if self.provider == "anthropic":
            try:
                import anthropic  # type: ignore
            except ImportError as exc:
                raise LLMFormatterError(
                    "anthropic package not installed. Run: pip install anthropic"
                ) from exc

            api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise LLMFormatterError(
                    "No Anthropic API key provided. Pass api_key in config or set ANTHROPIC_API_KEY."
                )

            self.client    = anthropic.Anthropic(api_key=api_key)
            self.model     = config.get("model") or "claude-sonnet-4-6"
            self._dispatch = self._call_anthropic

        elif self.provider in ("azure", "azure_openai", "openai_azure"):
            try:
                from openai import AzureOpenAI  # type: ignore
            except ImportError as exc:
                raise LLMFormatterError(
                    "openai package not installed. Run: pip install openai>=1.0"
                ) from exc

            api_key  = config.get("api_key") or ""
            endpoint = config.get("azure_endpoint") or config.get("api_base") or ""
            if not api_key or not endpoint:
                raise LLMFormatterError(
                    "Azure OpenAI requires both api_key and azure_endpoint in config."
                )

            api_version = config.get("azure_api_version") or config.get("api_version", "2024-02-01")
            self.client  = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=endpoint,
                api_version=api_version,
            )
            # Deployment name is used as the model identifier for Azure
            self.model     = (
                config.get("azure_deployment")
                or config.get("deployment")
                or config.get("model")
                or "gpt-4o"
            )
            self._dispatch = self._call_azure

        else:
            raise LLMFormatterError(f"Unsupported AI provider: '{self.provider}'")

        logger.info("LLMFormatter initialised: provider=%s model=%s", self.provider, self.model)

    # ── Backwards-compatible factory ──────────────────────────────────────────

    @classmethod
    def from_kwargs(
        cls,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        provider: str = "anthropic",
    ) -> "LLMFormatter":
        """Create from the old positional-keyword signature (Anthropic only)."""
        return cls({
            "provider": provider,
            "api_key":  api_key or "",
            "model":    model,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def generate_rca(
        self,
        incident: dict[str, Any],
        signals_summary: dict[str, Any],
        correlation_result: Any,
        cascade_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Call the configured LLM and return a structured RCA dict.

        incident:           {service, alert_name, description, time_window_minutes}
        signals_summary:    compact summary of what was collected
        correlation_result: CorrelationResult from CorrelationEngine
        cascade_result:     dict from CascadeDetector
        """
        prompt = self._build_prompt(incident, signals_summary, correlation_result, cascade_result)

        try:
            raw = self._dispatch(prompt)
            raw = _strip_fences(raw)
            rca_data = json.loads(raw)
            rca_data["model_used"]   = self.model
            rca_data["provider"]     = self.provider
            rca_data["generated_at"] = datetime.now(timezone.utc).isoformat()
            rca_data["ai_powered"]   = True
        except Exception as exc:
            logger.error("LLM RCA generation failed (%s): %s", self.provider, exc)
            rca_data = self._fallback_rca(incident, correlation_result, cascade_result, str(exc))

        return rca_data

    def render_html(
        self,
        rca_data: dict[str, Any],
        incident: dict[str, Any],
        signals_summary: dict[str, Any],
        correlation_result: Any,
        cascade_result: dict[str, Any],
        collection_errors: list[str],
    ) -> str:
        """Render the RCA data to a self-contained HTML report."""
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
            env = Environment(
                loader=FileSystemLoader(str(_TEMPLATES_DIR)),
                autoescape=select_autoescape(["html"]),
            )
            template = env.get_template("rca_report_html.jinja2")
            return template.render(
                rca=rca_data,
                incident=incident,
                signals=signals_summary,
                correlation=correlation_result,
                cascade=cascade_result,
                collection_errors=collection_errors,
                generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            )
        except Exception as exc:
            logger.error("HTML rendering failed: %s", exc)
            return self._minimal_html(rca_data, incident)

    # ─────────────────────────────────────────────────────────────────────────
    # Provider dispatch
    # ─────────────────────────────────────────────────────────────────────────

    def _call_anthropic(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            text = response.content[0].text
        except (AttributeError, IndexError):
            text = str(response)
        try:
            logger.info("Anthropic RCA complete (%s output tokens)", response.usage.output_tokens)
        except Exception:
            pass
        return text.strip()

    def _call_azure(self, prompt: str) -> str:
        messages = [
            {"role": "system",  "content": _SYSTEM_PROMPT},
            {"role": "user",    "content": prompt},
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=1.0,
        )
        text = response.choices[0].message.content or ""
        try:
            logger.info("Azure RCA complete (%s total tokens)", response.usage.total_tokens)
        except Exception:
            pass
        return text.strip()

    # ─────────────────────────────────────────────────────────────────────────
    # Prompt builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        incident: dict[str, Any],
        signals_summary: dict[str, Any],
        correlation_result: Any,
        cascade_result: dict[str, Any],
    ) -> str:
        rc_list = []
        for a in (correlation_result.root_cause_candidates or []):
            rc_list.append({
                "title":      a.title,
                "detail":     a.detail,
                "severity":   a.severity,
                "confidence": round(a.confidence, 2),
                "service":    a.service,
                "category":   a.category,
                "evidence":   a.evidence[:3],
            })

        context = {
            "incident": incident,
            "signals_summary": signals_summary,
            "root_cause_candidates": rc_list,
            "cascade": {
                "blast_radius":        cascade_result.get("blast_radius", 0),
                "direct_dependents":   cascade_result.get("direct_dependents", []),
                "indirect_dependents": cascade_result.get("indirect_dependents", []),
                "cascade_chain":       cascade_result.get("cascade_chain", []),
            },
            "totals": {
                "firing_alerts":     correlation_result.total_firing_alerts,
                "error_logs":        correlation_result.total_error_logs,
                "slow_traces":       correlation_result.total_slow_traces,
                "unhealthy_targets": correlation_result.total_unhealthy_targets,
            },
        }

        return f"""Analyse this incident and return a structured RCA JSON.

INCIDENT CONTEXT:
{json.dumps(context, indent=2, default=str)}

Return ONLY a JSON object with this exact schema:
{{
  "executive_summary": "2-3 sentence non-technical summary of what happened and its impact",
  "root_cause": {{
    "title": "concise root cause title",
    "description": "detailed technical explanation with specific metric values",
    "confidence": 0.0-1.0,
    "category": "infrastructure|application|network|database|configuration|external"
  }},
  "contributing_factors": [
    {{"factor": "...", "description": "...", "severity": "high|medium|low"}}
  ],
  "impact_assessment": {{
    "severity": "P1|P2|P3|P4",
    "affected_services": [...],
    "blast_radius": "description of how wide the impact spread",
    "estimated_user_impact": "...",
    "business_impact": "..."
  }},
  "timeline": [
    {{"time": "T+0m", "event": "..."}}
  ],
  "remediation": {{
    "immediate_actions": ["...", "..."],
    "short_term_fixes": ["...", "..."],
    "long_term_improvements": ["...", "..."]
  }},
  "prevention": ["...", "..."],
  "observability_gaps": ["signal or alert that was missing and would have helped"]
}}"""

    # ─────────────────────────────────────────────────────────────────────────
    # Fallback + minimal HTML
    # ─────────────────────────────────────────────────────────────────────────

    def _fallback_rca(
        self,
        incident: dict[str, Any],
        correlation_result: Any,
        cascade_result: dict[str, Any],
        error: str,
    ) -> dict[str, Any]:
        """Return a best-effort RCA dict when LLM is unavailable."""
        candidates = correlation_result.root_cause_candidates or []
        top = candidates[0] if candidates else None

        return {
            "executive_summary": (
                f"Automated analysis detected {correlation_result.total_firing_alerts} "
                f"firing alerts, {correlation_result.total_error_logs} error log entries, "
                f"and {correlation_result.total_slow_traces} slow traces. "
                f"AI narrative generation was unavailable ({error})."
            ),
            "root_cause": {
                "title":       top.title if top else "Unknown — no anomalies detected",
                "description": top.detail if top else "No significant anomalies found in the time window.",
                "confidence":  round(top.confidence, 2) if top else 0.0,
                "category":    "unknown",
            },
            "contributing_factors": [
                {"factor": a.title, "description": a.detail, "severity": a.severity}
                for a in candidates[1:4]
            ],
            "impact_assessment": {
                "severity":              "P2" if correlation_result.total_firing_alerts > 0 else "P3",
                "affected_services":     cascade_result.get("all_affected", []),
                "blast_radius":          f"{cascade_result.get('blast_radius', 0)} services",
                "estimated_user_impact": "Unknown — manual review required",
                "business_impact":       "Unknown — manual review required",
            },
            "timeline":    [{"time": "T+0m", "event": "Incident signals collected by RCA Agent"}],
            "remediation": {
                "immediate_actions":      ["Review firing alerts and acknowledge in Alertmanager"],
                "short_term_fixes":       ["Investigate root cause service for recent deployments"],
                "long_term_improvements": ["Add runbooks to all firing alerts"],
            },
            "prevention":         ["Improve alert coverage for identified gap areas"],
            "observability_gaps": ["AI analysis unavailable — manual gap review required"],
            "model_used":         "none (fallback)",
            "provider":           self.provider,
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "ai_powered":         False,
            "error":              error,
        }

    def _minimal_html(self, rca_data: dict[str, Any], incident: dict[str, Any]) -> str:
        """Bare-minimum HTML when Jinja2 rendering fails."""
        summary  = rca_data.get("executive_summary", "No summary available.")
        rc_title = rca_data.get("root_cause", {}).get("title", "Unknown")
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>RCA Report</title></head>"
            "<body style='font-family:sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem'>"
            f"<h1>RCA Report — {incident.get('service', 'unknown')}</h1>"
            f"<h2>Executive Summary</h2><p>{summary}</p>"
            f"<h2>Root Cause</h2><p><strong>{rc_title}</strong></p>"
            f"<pre>{json.dumps(rca_data, indent=2)}</pre>"
            "</body></html>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop opening fence line (```json or ```)
        start = 1 if lines[0].startswith("```") else 0
        text = "\n".join(lines[start:])
        # Drop closing fence
        if text.rstrip().endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()
