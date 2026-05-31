"""AYOSA AI Analyst — LLM-powered enrichment for investigation results.

Supports three LLM providers:
  - Anthropic (Claude) via the anthropic SDK
  - Azure OpenAI via the openai SDK (AzureOpenAI client)
  - OpenRouter via the openai SDK with a custom base URL

The analyst is ADDITIVE — it receives the deterministic evidence collected by
AYOSA adapters and returns a deeper, narrative-driven analysis that a purely
rule-based engine cannot produce:

  - Narrative root cause explanation with confidence reasoning
  - Risk assessment and blast-radius estimation
  - Prioritised remediation steps with estimated effort
  - Gap identification (signals missing from the investigation)
  - Executive summary suitable for incident channels / status pages
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an elite Site Reliability Engineer and incident commander with 15+ years of experience. You specialize in:

OBSERVABILITY TOOLING:
- Metrics: Prometheus, Datadog, Dynatrace, AppDynamics
- Logs: Elasticsearch, OpenSearch, Loki, Splunk, Datadog
- Traces: Jaeger, Tempo, Datadog, Dynatrace
- Alerts: Alertmanager, Grafana, PagerDuty, OpsGenie
- Dashboards: Grafana, Datadog

INCIDENT ANALYSIS EXPERTISE:
- Root cause analysis using the 5-Why methodology
- SLO burn-rate analysis and error budget management
- Service dependency mapping and blast-radius estimation
- Correlation of cross-signal evidence (metrics + logs + traces + alerts)
- Mean Time to Detect (MTTD) and Mean Time to Resolve (MTTR) optimization

RESPONSE FORMAT: Respond ONLY with a valid JSON object matching the schema in the user message. No markdown fences, no explanation outside the JSON."""

# ---------------------------------------------------------------------------
# Focused (evidence-bounded) system prompt
# ---------------------------------------------------------------------------

_FOCUSED_SYSTEM_PROMPT = """You are a Site Reliability Engineer performing incident root cause analysis.

STRICT RULES:
1. Use ONLY the evidence provided. Do not invent metrics, logs, traces, alerts, services, incidents, root causes, timelines, or remediation steps.
2. Be concise and precise — every sentence must add value. No padding, no repetition.
3. If evidence is insufficient for a confident conclusion, state that explicitly.

RESPONSE FORMAT: Respond ONLY with a valid JSON object matching the schema in the user message. No markdown fences, no explanation outside the JSON."""

# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

_RESPONSE_SCHEMA = {
    "narrative": "string: 2-3 paragraph narrative root cause analysis. Integrate evidence from all signals (metrics, logs, alerts, traces). Be specific — reference actual alert names, log patterns, and metric values from the evidence. Write for an on-call SRE.",
    "root_cause_confidence": "string: high|medium|low — your confidence in the root cause based on available evidence",
    "root_cause_reasoning": "string: step-by-step reasoning that led to the root cause conclusion (use 5-Why or similar)",
    "risk_assessment": {
        "severity": "string: critical|high|medium|low",
        "blast_radius": "string: description of which services/systems are affected or at risk",
        "user_impact": "string: describe the end-user or business impact",
        "escalation_required": "boolean: true if this warrants immediate escalation"
    },
    "remediation_steps": [
        {
            "step": "number: step order",
            "action": "string: specific action to take",
            "tool": "string: which observability tool or system to use",
            "expected_outcome": "string: what you expect to see after this action",
            "effort": "string: immediate|minutes|hours"
        }
    ],
    "signal_gaps": [
        "string: specific signal or data point that would improve this investigation"
    ],
    "executive_summary": "string: 2-3 sentence executive summary for a status page or Slack incident channel. Plain language, no jargon.",
    "follow_up_queries": [
        "string: specific query or check to run in the observability tools to confirm/refute the root cause"
    ]
}

_FOCUSED_RESPONSE_SCHEMA = {
    "executive_summary": (
        "string: 1-2 sentences summarising findings. Evidence-only. State gaps if data is sparse."
    ),
    "reasoning": (
        "string: bullet-point reasoning (max 5 points) from evidence to conclusion. Evidence-only."
    ),
    "missing_information": [
        "string: specific missing signal that would raise confidence (max 3 items)"
    ],
    "recommended_next_steps": [
        "string: concrete, tool-specific action (max 4 items)"
    ],
}

# ---------------------------------------------------------------------------
# Evidence summariser (reduces evidence to a compact LLM-friendly form)
# ---------------------------------------------------------------------------

def _build_prompt_context(investigation_result: dict[str, Any]) -> dict[str, Any]:
    """Distil investigation result into a compact context for the LLM prompt."""
    evidence = investigation_result.get("evidence", [])

    # Summarise evidence by signal type
    alerts_summary = []
    logs_summary = []
    metrics_summary = []
    traces_summary = []
    errors_summary = []

    for item in evidence:
        signal = item.get("signal", "unknown")
        status = item.get("status", "unknown")
        finding = item.get("finding", "")
        source = item.get("source", "unknown")

        entry = {"source": source, "finding": finding, "status": status}
        if item.get("query"):
            entry["query"] = item["query"]

        if status == "error":
            errors_summary.append(entry)
        elif signal == "alerts" or source == "alertmanager":
            raw = item.get("raw")
            if isinstance(raw, list) and raw:
                for alert in raw[:3]:
                    labels = alert.get("labels", {})
                    annotations = alert.get("annotations", {})
                    alerts_summary.append({
                        "alertname": labels.get("alertname"),
                        "severity": labels.get("severity"),
                        "service": labels.get("service"),
                        "summary": annotations.get("summary", ""),
                        "startsAt": alert.get("startsAt"),
                    })
            else:
                alerts_summary.append(entry)
        elif signal == "logs":
            raw = item.get("raw", {})
            if isinstance(raw, dict):
                hits = raw.get("hits", {}).get("hits", [])
                results = raw.get("results", [])
                sample_logs = []
                for hit in (hits or results)[:3]:
                    src = hit.get("_source", {})
                    msg = (
                        src.get("body") or src.get("message") or src.get("log")
                        or hit.get("_raw") or hit.get("message") or ""
                    )
                    if msg:
                        sample_logs.append(str(msg)[:200])
                if sample_logs:
                    entry["sample_log_messages"] = sample_logs
            logs_summary.append(entry)
        elif signal == "metrics":
            raw = item.get("raw", {})
            if isinstance(raw, dict):
                results = raw.get("data", {}).get("result", [])
                if results:
                    try:
                        entry["sample_value"] = results[0].get("value", [None, None])[1]
                        entry["metric_name"] = list(results[0].get("metric", {}).values())[:2]
                    except Exception:
                        pass
            metrics_summary.append(entry)
        elif signal == "traces":
            traces_summary.append(entry)

    return {
        "question": investigation_result.get("answer", "")[:500],
        "service": investigation_result.get("service"),
        "time_range": investigation_result.get("time_range"),
        "deterministic_confidence": investigation_result.get("confidence"),
        "signal_coverage": investigation_result.get("signal_coverage", {}),
        "missing_signals": investigation_result.get("missing_signals", []),
        "deterministic_root_cause": investigation_result.get("probable_root_cause", ""),
        "detected_patterns": investigation_result.get("detected_patterns", []),
        "impact": investigation_result.get("impact", ""),
        "active_alerts": alerts_summary[:5],
        "log_evidence": logs_summary[:5],
        "metric_evidence": metrics_summary[:5],
        "trace_evidence": traces_summary[:3],
        "adapter_errors": errors_summary[:5],
        "timeline": investigation_result.get("timeline", [])[:5],
        "suggested_actions": investigation_result.get("suggested_actions", []),
    }


# ---------------------------------------------------------------------------
# AYOSA AI Analyst class
# ---------------------------------------------------------------------------

class AyosaAIAnalystError(Exception):
    """Raised when AI analysis fails unrecoverably."""


class AyosaAIAnalyst:
    """Calls Anthropic, Azure OpenAI, or OpenRouter to enrich AYOSA investigation results."""

    def __init__(self, config: dict[str, Any]):
        """
        config keys:
          - provider: "anthropic" | "azure" | "openrouter"
          - api_key
          - model (Anthropic model name or OpenRouter model slug)
          - azure_endpoint (required for Azure)
          - azure_deployment (required for Azure, used as model name)
          - openrouter_model (e.g. "anthropic/claude-3.5-sonnet")
          - max_tokens, temperature
        """
        self.provider = (config.get("provider") or "anthropic").strip().lower()
        self.max_tokens = config.get("max_tokens", 1200)
        self.temperature = config.get("temperature", 0.3)
        self.model = config.get("model") or "claude-sonnet-4-6"

        if self.provider == "anthropic":
            try:
                import anthropic  # type: ignore
            except ImportError as exc:
                raise AyosaAIAnalystError(
                    "anthropic package not installed. Run: pip install anthropic"
                ) from exc

            api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise AyosaAIAnalystError(
                    "No Anthropic API key provided. Pass ai_api_key in the request."
                )
            self.client = anthropic.Anthropic(api_key=api_key)
            self._call = self._call_anthropic

        elif self.provider in ("azure", "azure_openai"):
            try:
                from openai import AzureOpenAI  # type: ignore
            except ImportError as exc:
                raise AyosaAIAnalystError(
                    "openai package not installed. Run: pip install openai>=1.0"
                ) from exc

            api_key = config.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY", "")
            api_base = (
                config.get("azure_endpoint")
                or config.get("api_base")
                or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
            )
            if not api_key or not api_base:
                raise AyosaAIAnalystError(
                    "Azure OpenAI requires api_key and azure_endpoint."
                )
            api_version = config.get("api_version", "2024-02-01")
            self.client = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=api_base,
                api_version=api_version,
            )
            self.model = (
                config.get("azure_deployment")
                or config.get("deployment")
                or config.get("model")
                or "gpt-4o"
            )
            self._call = self._call_openai_compatible

        elif self.provider == "openrouter":
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:
                raise AyosaAIAnalystError(
                    "openai package not installed. Run: pip install openai>=1.0"
                ) from exc

            api_key = config.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                raise AyosaAIAnalystError(
                    "No OpenRouter API key provided. Pass ai_api_key in the request."
                )
            self.model = (
                config.get("openrouter_model")
                or config.get("model")
                or "anthropic/claude-3.5-sonnet"
            )
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            self._call = self._call_openai_compatible

        else:
            raise AyosaAIAnalystError(f"Unsupported AI provider: {self.provider!r}")

    def analyze(self, investigation_result: dict[str, Any]) -> dict[str, Any]:
        """Run AI analysis on AYOSA investigation results. Returns enriched analysis dict."""
        logger.info(
            "Running AYOSA AI analysis with provider=%s model=%s",
            self.provider, self.model,
        )

        context = _build_prompt_context(investigation_result)
        user_message = self._build_user_message(context)

        try:
            raw_text = self._call(user_message)
        except AyosaAIAnalystError:
            raise
        except Exception as exc:
            logger.error("AYOSA AI analysis API call failed: %s", exc)
            return self._error_result(str(exc))

        try:
            parsed = self._parse_response(raw_text)
        except Exception as exc:
            logger.error("Failed to parse AYOSA AI response: %s", exc)
            logger.debug("Raw AI response snippet: %s", raw_text[:1000])
            return self._error_result(f"Response parse error: {exc}")

        parsed["provider"] = self.provider
        parsed["model"] = self.model
        return parsed

    # ------------------------------------------------------------------
    # Provider call implementations
    # ------------------------------------------------------------------

    def _call_anthropic(self, user_message: str, system_prompt: str = _SYSTEM_PROMPT) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        try:
            return response.content[0].text
        except Exception:
            return str(response)

    def _call_anthropic_streaming(self, user_message: str, system_prompt: str, on_chunk) -> str:
        """Anthropic streaming call — invokes on_chunk(text) for each token; returns full text."""
        full_text = ""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                on_chunk(text)
        return full_text

    def _call_openai_compatible(self, user_message: str, system_prompt: str = _SYSTEM_PROMPT) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return response.choices[0].message.content or ""

    def _call_openai_streaming(self, user_message: str, system_prompt: str, on_chunk) -> str:
        """OpenAI-compatible streaming call — invokes on_chunk(text) for each delta; returns full text."""
        full_text = ""
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if delta:
                full_text += delta
                on_chunk(delta)
        return full_text

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_user_message(self, context: dict[str, Any]) -> str:
        return f"""Analyze the following AYOSA investigation evidence and produce a deep root cause analysis.

## INVESTIGATION CONTEXT
```json
{json.dumps(context, indent=2, default=str)}
```

## REQUIRED RESPONSE SCHEMA
Respond ONLY with a JSON object matching this exact schema (no markdown fences, raw JSON only):
```json
{json.dumps(_RESPONSE_SCHEMA, indent=2)}
```

## ANALYSIS REQUIREMENTS

1. **Root Cause** — go beyond the deterministic finding. Use multi-signal correlation to identify the underlying cause, not just the symptom.
2. **Risk** — assess severity and blast radius based on the evidence. Consider whether this is a user-facing incident.
3. **Remediation** — provide ordered, actionable steps. Reference specific tools and queries.
4. **Gaps** — identify what evidence is missing that would increase confidence.
5. **Executive Summary** — write 2-3 sentences suitable for a Slack incident channel or status page.

Be specific. Reference actual alert names, log patterns, and metric values from the evidence provided."""

    def _parse_response(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            text = "\n".join(lines[start:end])

        return json.loads(text)

    def _error_result(self, error_message: str) -> dict[str, Any]:
        return {
            "narrative": f"AI analysis could not be completed: {error_message}",
            "root_cause_confidence": "low",
            "root_cause_reasoning": "AI analysis failed — see deterministic findings above.",
            "risk_assessment": {
                "severity": "unknown",
                "blast_radius": "unknown",
                "user_impact": "unknown",
                "escalation_required": False,
            },
            "remediation_steps": [],
            "signal_gaps": [],
            "executive_summary": "AI analysis was unavailable. Review the deterministic evidence above.",
            "follow_up_queries": [],
            "error": error_message,
            "provider": self.provider,
            "model": self.model,
        }

    # ------------------------------------------------------------------
    # Focused, evidence-bounded analysis
    # ------------------------------------------------------------------

    def analyze_focused(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run a focused LLM analysis that is strictly bounded to the provided evidence.

        The LLM is explicitly instructed not to invent metrics, logs, traces,
        alerts, services, incidents, root causes, timelines, or remediation
        steps beyond what is present in *context*.
        """
        logger.info(
            "Running AYOSA focused LLM analysis with provider=%s model=%s",
            self.provider,
            self.model,
        )

        user_message = (
            "Analyze ONLY the following evidence from an AYOSA investigation. "
            "Do NOT invent or assume any information not present in this evidence.\n\n"
            "## EVIDENCE\n"
            f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
            "## REQUIRED RESPONSE SCHEMA\n"
            "Respond ONLY with a JSON object matching this exact schema "
            "(no markdown fences, raw JSON only):\n"
            f"```json\n{json.dumps(_FOCUSED_RESPONSE_SCHEMA, indent=2)}\n```\n\n"
            "## IMPORTANT CONSTRAINTS\n"
            "- Do not invent metrics, logs, traces, alerts, services, incidents, "
            "root causes, timelines, or remediation steps.\n"
            "- Base ALL statements on the evidence provided above.\n"
            "- If the evidence is insufficient, clearly state what is missing "
            "rather than guessing.\n"
            "- Reference specific alert names, log messages, and metric values "
            "from the evidence when available."
        )

        try:
            raw_text = self._call(user_message, _FOCUSED_SYSTEM_PROMPT)
        except Exception as exc:
            logger.error("AYOSA focused LLM call failed: %s", exc)
            return self._focused_error_result(str(exc))

        try:
            parsed = self._parse_response(raw_text)
        except Exception as exc:
            logger.error("Failed to parse focused LLM response: %s", exc)
            logger.debug("Raw focused AI response snippet: %s", raw_text[:1000])
            return self._focused_error_result(f"Response parse error: {exc}")

        parsed["provider"] = self.provider
        parsed["model"] = self.model
        return parsed

    def _focused_error_result(self, error_message: str) -> dict[str, Any]:
        return {
            "executive_summary": (
                "LLM analysis could not be completed. "
                "Review the deterministic findings above."
            ),
            "reasoning": f"LLM analysis failed: {error_message}",
            "missing_information": [],
            "recommended_next_steps": [],
            "error": error_message,
            "provider": self.provider,
            "model": self.model,
        }

    # ------------------------------------------------------------------
    # Streaming focused analysis (used by investigate_stream)
    # ------------------------------------------------------------------

    def analyze_focused_streaming(
        self,
        context: dict[str, Any],
        on_chunk,
    ) -> dict[str, Any]:
        """Like analyze_focused() but streams raw tokens via on_chunk(text).

        on_chunk is called synchronously from the LLM provider's streaming
        loop.  The caller (service._stream_llm_analysis) bridges this to
        asyncio via loop.call_soon_threadsafe so the UI receives live text
        while the JSON is still being assembled.

        Returns the fully parsed analysis dict once generation is complete.
        """
        logger.info(
            "Running AYOSA focused streaming LLM analysis with provider=%s model=%s",
            self.provider, self.model,
        )

        user_message = (
            "Analyze ONLY the following evidence from an AYOSA investigation. "
            "Do NOT invent or assume any information not present in this evidence.\n\n"
            "## EVIDENCE\n"
            f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
            "## REQUIRED RESPONSE SCHEMA\n"
            "Respond ONLY with a JSON object matching this exact schema "
            "(no markdown fences, raw JSON only):\n"
            f"```json\n{json.dumps(_FOCUSED_RESPONSE_SCHEMA, indent=2)}\n```\n\n"
            "## IMPORTANT CONSTRAINTS\n"
            "- Evidence-only. No invented facts.\n"
            "- Be concise — 1-2 sentence summary, max 5 reasoning bullet points, max 4 next steps.\n"
            "- If evidence is insufficient, state what is missing rather than guessing."
        )

        if self.provider == "anthropic":
            stream_fn = self._call_anthropic_streaming
        else:
            stream_fn = self._call_openai_streaming

        try:
            raw_text = stream_fn(user_message, _FOCUSED_SYSTEM_PROMPT, on_chunk)
        except Exception as exc:
            logger.error("AYOSA focused streaming LLM call failed: %s", exc)
            return self._focused_error_result(str(exc))

        try:
            parsed = self._parse_response(raw_text)
        except Exception as exc:
            logger.error("Failed to parse focused streaming LLM response: %s", exc)
            return self._focused_error_result(f"Response parse error: {exc}")

        parsed["provider"] = self.provider
        parsed["model"] = self.model
        return parsed
