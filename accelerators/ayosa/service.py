from __future__ import annotations

import asyncio
import inspect
import logging
import re
import types
from datetime import datetime
from typing import Any, AsyncGenerator

from accelerators.ayosa.registry import ADAPTERS

logger = logging.getLogger(__name__)

# ── Intent keywords — checked in priority order (most-specific first) ────────
# Stage 1 — Observability Copilot intent planner

_INTENT_KEYWORDS: dict[str, list[str]] = {
    # Most specific patterns first
    "current_time":           ["what time", "current time", "what is the time", "what's the time",
                                "what date", "today's date", "current date"],
    "healthy_services_list":  ["which services are healthy", "list services", "list all services",
                                "show services", "show all services", "healthy services",
                                "list healthy"],
    "environment_health":     ["environment health", "overall health", "overall status",
                                "system health", "infrastructure health", "platform health",
                                "everything ok", "anything wrong", "what's happening",
                                "what is happening", "what happened"],
    "latency_issues":         ["latency", "slow", "p95", "p99", "response time", "throughput",
                                "slow response", "slow request"],
    "latest_error":           ["latest error", "last error", "most recent error", "recent error",
                                "last failure", "latest failure", "last exception",
                                "latest exception", "last crash"],
    "active_alerts":          ["alert", "alerts", "firing", "alarm", "incident", "incidents",
                                "pagerduty", "opsgenie", "any alerts", "active alert",
                                "is there an alert"],
    "trace_lookup":           ["trace", "traces", "span", "spans", "distributed trace"],
    "dashboard_lookup":       ["dashboard", "dashboards", "grafana board", "panel", "visualization"],
    "service_health":         ["health of", "is healthy", "health check", "service health",
                                "is it up", "is it down", "service status", "how is",
                                "status of", "check health"],
    "error_investigation":    ["error", "errors", "failed", "failure", "failures", "exception",
                                "exceptions", "stacktrace", "stack trace", "broken", "not working"],
}

# ── Query planner ─────────────────────────────────────────────────────────────

# Which tool provides which signal type (Stage 2 — capability mapping per spec)
_SIGNAL_TOOLS: dict[str, list[str]] = {
    "metrics":    ["prometheus", "datadog", "dynatrace", "appdynamics"],
    "logs":       ["elasticsearch", "opensearch", "splunk", "loki"],
    "alerts":     ["alertmanager", "grafana", "splunk", "datadog", "dynatrace"],
    "traces":     ["jaeger", "tempo", "datadog", "dynatrace", "appdynamics"],
    "dashboards": ["grafana", "splunk", "datadog", "dynatrace"],
}

# Which signal types each intent needs
_INTENT_SIGNALS: dict[str, list[str]] = {
    "current_time":                   [],
    "environment_health":             ["metrics", "alerts"],          # no generic error-log scan
    "service_health":                 ["metrics", "alerts", "logs", "traces"],
    "healthy_services_list":          ["metrics", "alerts"],          # no generic error-log scan
    "latency_issues":                 ["metrics", "traces"],          # no generic error-log scan
    "latest_error":                   ["logs"],
    "error_investigation":            ["logs", "metrics", "alerts"],
    "active_alerts":                  ["alerts"],
    "trace_lookup":                   ["traces"],
    "dashboard_lookup":               ["dashboards"],
    "general_observability_question": ["metrics", "logs", "alerts", "traces"],
}

# Human-readable focus hint per intent (used by UI and downstream LLM prompts)
_INTENT_QUERY_FOCUS: dict[str, str] = {
    "current_time":                   "Return current server time. No tool query needed.",
    "environment_health":             "Aggregate health across all services using active alerts, error rates, and recent logs.",
    "service_health":                 "Assess the named service using metrics, alerts, logs, and traces.",
    "healthy_services_list":          "Enumerate services with no firing alerts and stable error rates.",
    "latency_issues":                 "Inspect latency percentiles (p95/p99) and slow traces.",
    "latest_error":                   "Return the most recent error log entry.",
    "error_investigation":            "Investigate error patterns across logs, error-rate metrics, and related alerts.",
    "active_alerts":                  "List currently firing alerts and their severity.",
    "trace_lookup":                   "Look up traces or spans matching the request.",
    "dashboard_lookup":               "Locate relevant dashboards for the question.",
    "general_observability_question": "Broad observability query — gather available signals.",
}

# Time-range extraction patterns — first match wins
_TIME_RANGE_PATTERNS = [
    (r"\blast\s+(\d+)\s*hours?\b",            lambda m: f"{m.group(1)}h"),
    (r"\blast\s+(\d+)h\b",                   lambda m: f"{m.group(1)}h"),
    (r"\blast\s+(\d+)\s*minutes?\b",          lambda m: f"{m.group(1)}m"),
    (r"\blast\s+(\d+)m\b",                   lambda m: f"{m.group(1)}m"),
    (r"\bpast\s+(\d+)\s*hours?\b",            lambda m: f"{m.group(1)}h"),
    (r"\bpast\s+(\d+)h\b",                   lambda m: f"{m.group(1)}h"),
    (r"\bpast\s+(\d+)\s*minutes?\b",          lambda m: f"{m.group(1)}m"),
    (r"\bpast\s+(\d+)m\b",                   lambda m: f"{m.group(1)}m"),
    (r"\bin\s+the\s+last\s+(\d+)\s*hours?\b", lambda m: f"{m.group(1)}h"),
    (r"\bin\s+the\s+last\s+(\d+)\s*min\w*\b", lambda m: f"{m.group(1)}m"),
]


def _extract_time_range_from_message(msg: str) -> str | None:
    """Return e.g. '1h' / '30m' inferred from free text, or None."""
    msg = msg.lower()
    for pattern, fn in _TIME_RANGE_PATTERNS:
        m = re.search(pattern, msg)
        if m:
            return fn(m)
    return None


# Priority order for keyword matching — most specific first
_INTENT_PRIORITY = [
    "current_time",
    "healthy_services_list",
    "environment_health",
    "latency_issues",
    "latest_error",
    "active_alerts",
    "trace_lookup",
    "dashboard_lookup",
    "service_health",
    "error_investigation",
]


def _classify_intent_standalone(message: str) -> str:
    """Pure-function intent classifier — no AyosaService instance needed."""
    msg = message.lower().strip()
    for intent in _INTENT_PRIORITY:
        if any(kw in msg for kw in _INTENT_KEYWORDS.get(intent, [])):
            return intent
    return "general_observability_question"


def build_ayosa_plan(
    message: str,
    service: str | None,
    time_range: str,
    available_tools: list[Any],
) -> dict[str, Any]:
    """Build a structured investigation plan from user intent + validated tools.

    Returns a dict with the Stage 1 contract:
      intent, service, time_range,
      required_signals, selected_tools, query_focus,
      should_query_metrics, should_query_logs, should_query_alerts,
      should_query_traces, should_query_dashboards
    plus extra diagnostic fields preserved for backwards compat:
      covered_signals, missing_signals, skipped_tools, explanation
    """
    intent = _classify_intent_standalone(message)
    inferred_tr = _extract_time_range_from_message(message)
    resolved_tr = inferred_tr or time_range

    required_signals: list[str] = _INTENT_SIGNALS.get(intent, [])
    available_names = {t.tool.lower().strip() for t in available_tools}

    selected: list[str] = []
    covered: list[str] = []
    for signal in required_signals:
        for tool_name in _SIGNAL_TOOLS.get(signal, []):
            if tool_name in available_names and tool_name not in selected:
                selected.append(tool_name)
                covered.append(signal)

    missing = [s for s in required_signals if s not in covered]
    skipped = [t.tool for t in available_tools if t.tool.lower() not in selected]

    required_set = set(required_signals)

    parts: list[str] = [f"Intent: {intent.replace('_', ' ')}"]
    if service:
        parts.append(f"service={service}")
    parts.append(
        f"querying {', '.join(selected)}" if selected
        else "no observability queries needed"
    )
    if missing:
        parts.append(f"missing signals: {', '.join(missing)}")

    return {
        # ── Stage 1 contract ──
        "intent":                  intent,
        "service":                 service,
        "time_range":              resolved_tr,
        "required_signals":        required_signals,
        "selected_tools":          selected,
        "query_focus":             _INTENT_QUERY_FOCUS.get(intent, ""),
        "should_query_metrics":    "metrics"    in required_set,
        "should_query_logs":       "logs"       in required_set,
        "should_query_alerts":     "alerts"     in required_set,
        "should_query_traces":     "traces"     in required_set,
        "should_query_dashboards": "dashboards" in required_set,
        # ── Diagnostic extras (used by existing UI/streaming code) ──
        "covered_signals":         list(dict.fromkeys(covered)),
        "missing_signals":         missing,
        "skipped_tools":           skipped,
        "explanation":             ". ".join(parts) + ".",
    }




SIGNAL_CAPABILITIES = {
    "prometheus": ["metrics"],
    "alertmanager": ["alerts"],
    "elasticsearch": ["logs"],
    "opensearch": ["logs"],
    "splunk": ["logs", "alerts"],
    "grafana": ["dashboards", "alerts"],
    "jaeger": ["traces"],
    "tempo": ["traces"],
    "loki": ["logs"],
    "datadog": ["metrics", "logs", "traces", "dashboards", "alerts"],
    "dynatrace": ["metrics", "logs", "traces", "dashboards", "alerts"],
    "appdynamics": ["metrics", "traces", "dashboards", "alerts"],
}

EXPECTED_SIGNALS = ["metrics", "logs", "alerts", "traces"]


class AyosaService:

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------

    def _classify_intent(self, message: str) -> str:
        """Delegate to the module-level pure-function classifier."""
        return _classify_intent_standalone(message)

    def _no_matching_tools_answer(self, plan: dict[str, Any]) -> str:
        """Return a helpful message when no validated tool covers the required signals."""
        intent = plan["intent"].replace("_", " ")
        missing = plan["missing_signals"]
        if missing:
            suggestions = "; ".join(
                f"{s}: try {', '.join(_SIGNAL_TOOLS.get(s, [])[:3])}"
                for s in missing
            )
            return (
                f"To answer your '{intent}' question I need access to "
                f"{', '.join(missing)} data, but none of the validated tools provide "
                f"these signals. Consider adding: {suggestions}."
            )
        return (
            f"Your question was classified as '{intent}'. "
            "No observability tool queries are needed to answer this."
        )

    def _quick_result(self, request: Any, answer: str, intent: str) -> dict[str, Any]:
        """Return a minimal result dict without running any tool queries."""
        return {
            "answer": answer,
            "service": request.service,
            "time_range": request.time_range,
            "confidence": 1.0,
            "intent": intent,
            "signal_coverage": {},
            "missing_signals": [],
            "probable_root_cause": "",
            "impact": "",
            "detected_patterns": [],
            "timeline": [],
            "related_artifacts": [],
            "evidence": [],
            "suggested_actions": [],
            "ai_analysis": None,
            "charts": [],
            "llm_analysis": None,
            "incident_snapshot": None,
        }

    # ------------------------------------------------------------------
    # Single-tool query helper (used by both sync and streaming paths)
    # ------------------------------------------------------------------

    def _query_single_tool(
        self,
        tool: Any,
        request: Any,
        plan: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one adapter's investigate() and return its evidence list.
        `request` may be a Pydantic model or a SimpleNamespace.
        Passes `plan=` to adapters that declare it (backwards-compatible).
        """
        tool_key = tool.tool.lower().strip()
        adapter_cls = ADAPTERS.get(tool_key)
        if not adapter_cls:
            return [{
                "source": tool.tool,
                "signal": "unknown",
                "finding": f"No AYOSA adapter found for tool: {tool.tool}",
                "query": None,
                "status": "error",
                "raw": None,
            }]
        adapter = adapter_cls(base_url=tool.base_url, auth_token=tool.auth_token)
        kwargs: dict[str, Any] = {
            "service":    request.service,
            "time_range": request.time_range,
            "message":    request.message,
        }
        # Pass `plan` only if the adapter's investigate() accepts it (or **kwargs)
        if plan is not None:
            try:
                sig = inspect.signature(adapter.investigate)
                params = sig.parameters
                accepts_plan = (
                    "plan" in params
                    or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
                )
            except (TypeError, ValueError):
                accepts_plan = False
            if accepts_plan:
                kwargs["plan"] = plan
        return adapter.investigate(**kwargs)

    # ------------------------------------------------------------------
    # Streaming investigation (yields SSE-friendly dicts)
    # ------------------------------------------------------------------

    async def investigate_stream(self, request: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator that yields SSE events while running the investigation.

        Event shapes:
          {"type": "plan",      "data": dict}                              — first event
          {"type": "step",      "index": int, "label": str, "status": ...} — per tool
          {"type": "llm_chunk", "text": str}                               — LLM tokens
          {"type": "result",    "data": dict}                              — final result
          {"type": "error",     "message": str}                            — on failure
        """
        intent = self._classify_intent(request.message)

        # Fast-paths: answer immediately with no tool queries
        if intent == "current_time":
            now = datetime.now()
            yield {"type": "result", "data": self._quick_result(
                request,
                answer=f"The current server time is {now.strftime('%A, %d %B %Y at %H:%M:%S')} (server local time).",
                intent=intent,
            )}
            return

        if intent == "general_chat":
            tool_count = len(request.tools)
            yield {"type": "result", "data": self._quick_result(
                request,
                answer=(
                    f"Hi! I'm AYOSA — Ask Your Observability Stack Anything. "
                    f"I currently have access to {tool_count} validated tool"
                    f"{'s' if tool_count != 1 else ''}. "
                    "You can ask me things like: 'What is the health of my environment?', "
                    "'Show error trend for the last 1h', 'Are there any active alerts?', "
                    "or 'What was the last error?'"
                ),
                intent=intent,
            )}
            return

        # ── Build query plan ──
        plan = build_ayosa_plan(request.message, request.service, request.time_range, request.tools)
        yield {"type": "plan", "data": plan}

        effective = types.SimpleNamespace(
            message=request.message,
            service=request.service,
            time_range=plan["time_range"],
            tools=request.tools,
            ai=getattr(request, "ai", None),
        )

        selected_names = set(plan["selected_tools"])
        n_tools = len(request.tools)

        # ── Step 0 + 1: intent classify + tool selection (instant) ──
        yield {"type": "step", "index": 0, "label": "Understanding question", "status": "done"}
        yield {"type": "step", "index": 1, "label": "Selecting tools",        "status": "done"}

        # ── Steps 2…n+1: per-tool, skipping irrelevant ones ──
        evidence: list[dict[str, Any]] = []
        for i, tool in enumerate(request.tools):
            step_idx  = i + 2
            tool_name = tool.tool.lower()

            if tool_name not in selected_names:
                yield {"type": "step", "index": step_idx, "label": f"Querying {tool.tool}", "status": "skipped"}
                continue

            yield {"type": "step", "index": step_idx, "label": f"Querying {tool.tool}", "status": "running"}
            try:
                tool_evidence = await asyncio.to_thread(self._query_single_tool, tool, effective, plan)
                evidence.extend(tool_evidence)
                yield {"type": "step", "index": step_idx, "label": f"Querying {tool.tool}", "status": "done"}
            except Exception as exc:
                logger.warning("Tool query failed for %s: %s", tool.tool, exc)
                evidence.append({
                    "source": tool.tool, "signal": "unknown",
                    "finding": f"Adapter failed: {exc}", "query": None,
                    "status": "error", "raw": None,
                })
                yield {"type": "step", "index": step_idx, "label": f"Querying {tool.tool}", "status": "error"}

        # ── Correlation step ──
        correlate_idx = n_tools + 2
        yield {"type": "step", "index": correlate_idx, "label": "Correlating evidence", "status": "running"}
        signal_coverage = self._build_signal_coverage(
            [t for t in request.tools if t.tool.lower() in selected_names]
        )
        # Stage 2: report missing signals relative to the plan's required signals
        missing_signals = plan["missing_signals"]
        result = await asyncio.to_thread(
            self._build_deterministic_result, evidence, effective, signal_coverage, missing_signals
        )
        result["plan"] = plan
        yield {"type": "step", "index": correlate_idx, "label": "Correlating evidence", "status": "done"}

        # ── AI analysis step (streaming LLM text) ──
        ai_cfg = getattr(request, "ai", None)
        if ai_cfg and ai_cfg.enabled:
            ai_idx = n_tools + 3
            yield {"type": "step", "index": ai_idx, "label": "Generating AI analysis", "status": "running"}

            llm_result: dict[str, Any] | None = None
            async for event in self._stream_llm_analysis(ai_cfg, result):
                if event["type"] == "llm_chunk":
                    yield event
                elif event["type"] == "llm_done":
                    llm_result = event.get("data")

            if llm_result:
                result["llm_analysis"] = llm_result
            result["ai_analysis"] = await asyncio.to_thread(self._run_ai_analysis, ai_cfg, result)
            yield {"type": "step", "index": ai_idx, "label": "Generating AI analysis", "status": "done"}
        else:
            yield {"type": "step", "index": n_tools + 3, "label": "Generating answer", "status": "done"}

        yield {"type": "result", "data": result}

    # ------------------------------------------------------------------
    # Deterministic result builder (sync, called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _build_deterministic_result(
        self,
        evidence: list[dict[str, Any]],
        request: Any,
        signal_coverage: dict[str, list[str]],
        missing_signals: list[str],
    ) -> dict[str, Any]:
        """Build the complete deterministic result dict from pre-collected evidence."""
        ok_count     = len([e for e in evidence if e.get("status") == "ok"])
        pending_count = len([e for e in evidence if e.get("status") == "not_implemented"])
        error_count  = len([e for e in evidence if e.get("status") == "error"])

        confidence = self._calculate_confidence(evidence, missing_signals)
        intent     = self._classify_intent(request.message)

        answer = self._summarize_evidence(
            evidence=evidence,
            service=request.service,
            time_range=request.time_range,
            ok_count=ok_count,
            pending_count=pending_count,
            error_count=error_count,
            signal_coverage=signal_coverage,
            missing_signals=missing_signals,
        )

        alerts  = self._extract_active_alerts(evidence)
        logs    = self._extract_log_hits(evidence)
        metrics = self._extract_metric_values(evidence)

        result: dict[str, Any] = {
            "answer":             answer,
            "service":            request.service,
            "time_range":         request.time_range,
            "confidence":         confidence,
            "intent":             intent,
            "signal_coverage":    signal_coverage,
            "missing_signals":    missing_signals,
            "probable_root_cause": self._infer_probable_cause(alerts, logs, metrics, service=request.service),
            "impact":             self._summarize_impact(evidence, request.service),
            "detected_patterns":  self._detect_patterns(evidence),
            "timeline":           self._build_timeline(evidence),
            "related_artifacts":  self._related_artifacts(evidence),
            "evidence":           evidence,
            "suggested_actions":  self._suggest_actions(evidence, missing_signals),
            "ai_analysis":        None,
            "charts":             [],
            "llm_analysis":       None,
            "incident_snapshot":  None,
        }

        result["charts"] = self._collect_charts(
            tools=request.tools,
            service=request.service,
            time_range=request.time_range,
        )
        result["incident_snapshot"] = self._build_incident_snapshot(result)
        return result

    # ------------------------------------------------------------------
    # LLM streaming helper
    # ------------------------------------------------------------------

    async def _stream_llm_analysis(
        self, ai_cfg: Any, result: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator: yields {type: llm_chunk, text} events then {type: llm_done, data: parsed_result}."""
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def on_chunk(text: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "llm_chunk", "text": text})

        def sync_work() -> None:
            try:
                from accelerators.ayosa.llm.analyst import AyosaAIAnalyst
                analyst = AyosaAIAnalyst({
                    "provider":         ai_cfg.provider or "anthropic",
                    "api_key":          ai_cfg.api_key,
                    "model":            ai_cfg.model,
                    "azure_endpoint":   ai_cfg.azure_endpoint,
                    "azure_deployment": ai_cfg.azure_deployment,
                    "openrouter_model": ai_cfg.openrouter_model,
                })
                context  = self._generate_llm_context(result)
                analysis = analyst.analyze_focused_streaming(context, on_chunk)
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "llm_done", "data": analysis})
            except Exception as exc:
                logger.error("AYOSA streaming LLM failed: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "llm_done", "data": {
                    "executive_summary": f"AI analysis failed: {exc}",
                    "reasoning": "", "missing_information": [],
                    "recommended_next_steps": [], "error": str(exc),
                }})

        import threading
        thread = threading.Thread(target=sync_work, daemon=True)
        thread.start()

        while True:
            event = await queue.get()
            yield event
            if event["type"] == "llm_done":
                break

    def investigate(self, request):
        intent = self._classify_intent(request.message)

        # ── Fast-path: current_time ──
        if intent == "current_time":
            now = datetime.now()
            return self._quick_result(
                request,
                answer=f"The current server time is {now.strftime('%A, %d %B %Y at %H:%M:%S')} (server local time).",
                intent=intent,
            )

        # ── Fast-path: general_chat ──
        if intent == "general_chat":
            tool_count = len(request.tools)
            return self._quick_result(
                request,
                answer=(
                    f"Hi! I'm AYOSA — Ask Your Observability Stack Anything. "
                    f"I currently have access to {tool_count} validated tool"
                    f"{'s' if tool_count != 1 else ''}. "
                    "You can ask me things like: 'What is the health of my environment?', "
                    "'Show error trend for the last 1h', 'Are there any active alerts?', "
                    "or 'What was the last error?'"
                ),
                intent=intent,
            )

        # ── Build query plan (intent + tool selection + time-range resolution) ──
        plan = build_ayosa_plan(request.message, request.service, request.time_range, request.tools)

        # Effective request: may use a time-range inferred from the message
        effective = types.SimpleNamespace(
            message=request.message,
            service=request.service,
            time_range=plan["time_range"],
            tools=request.tools,
            ai=getattr(request, "ai", None),
        )

        # Select only tools needed for this intent
        selected_names = set(plan["selected_tools"])
        tools_to_query = [t for t in request.tools if t.tool.lower() in selected_names]

        # If intent requires signals but no matching tool was validated, respond gracefully
        if plan["required_signals"] and not tools_to_query:
            result = self._quick_result(request, answer=self._no_matching_tools_answer(plan), intent=intent)
            result["plan"] = plan
            return result

        signal_coverage = self._build_signal_coverage(tools_to_query)
        # Stage 2: report missing signals from the plan (intent-relative), not the global default
        missing_signals = plan["missing_signals"]

        evidence: list[dict[str, Any]] = []
        for tool in tools_to_query:
            try:
                evidence.extend(self._query_single_tool(tool, effective, plan=plan))
            except Exception as exc:
                evidence.append({
                    "source": tool.tool, "signal": "unknown",
                    "finding": f"Adapter failed for {tool.tool}: {exc}",
                    "query": None, "status": "error", "raw": None,
                })

        result = self._build_deterministic_result(evidence, effective, signal_coverage, missing_signals)
        result["plan"] = plan

        # Optional LLM enrichment
        ai_cfg = getattr(request, "ai", None)
        if ai_cfg and ai_cfg.enabled:
            result["ai_analysis"] = self._run_ai_analysis(ai_cfg, result)
            result["llm_analysis"] = self._generate_llm_analysis(ai_cfg, result)

        return result

    # ------------------------------------------------------------------
    # Chart collection
    # ------------------------------------------------------------------

    def _collect_charts(
        self,
        tools: list[Any],
        service: str | None,
        time_range: str,
    ) -> list[dict[str, Any]]:
        """Call get_charts() on every adapter that supports it.
        Stage 3: drop charts that came back with no data points so the UI does
        not render empty panels.
        """
        charts: list[dict[str, Any]] = []
        for tool in tools:
            tool_key = tool.tool.lower().strip()
            adapter_cls = ADAPTERS.get(tool_key)
            if not adapter_cls:
                continue
            adapter = adapter_cls(base_url=tool.base_url, auth_token=tool.auth_token)
            if not hasattr(adapter, "get_charts"):
                continue
            try:
                charts.extend(adapter.get_charts(service=service, time_range=time_range))
            except Exception as exc:
                logger.warning("Chart collection failed for %s: %s", tool.tool, exc)
        # Drop charts with no data points — never render empty panels.
        return [c for c in charts if c.get("data")]

    # ------------------------------------------------------------------
    # Incident snapshot
    # ------------------------------------------------------------------

    def _build_incident_snapshot(self, result: dict[str, Any]) -> dict[str, Any]:
        """Assemble a compact, structured incident snapshot from investigation data."""
        evidence = result.get("evidence", [])
        top_findings = [
            item["finding"]
            for item in evidence
            if item.get("status") == "ok" and item.get("finding")
        ]
        timeline = result.get("timeline", [])
        return {
            "root_cause": result.get("probable_root_cause", ""),
            "impact": result.get("impact", ""),
            "confidence": result.get("confidence", 0.0),
            "coverage": result.get("signal_coverage", {}),
            "top_findings": top_findings[:5],
            "recommended_actions": result.get("suggested_actions", [])[:5],
            "timeline_summary": [
                {
                    "timestamp": item.get("timestamp"),
                    "source": item.get("source"),
                    "event": (item.get("event") or "")[:150],
                    "severity": item.get("severity"),
                }
                for item in timeline[:5]
            ],
        }

    # ------------------------------------------------------------------
    # LLM context builder (compact evidence for LLM consumption)
    # ------------------------------------------------------------------

    def _generate_llm_context(self, result: dict[str, Any]) -> dict[str, Any]:
        """Prepare a compact, evidence-only dict for LLM consumption.

        Only facts already present in the investigation result are included.
        This dict is passed verbatim to the LLM prompt so the model cannot
        reference anything outside it.
        """
        evidence = result.get("evidence", [])

        alerts: list[dict[str, Any]] = []
        log_samples: list[dict[str, Any]] = []
        metric_samples: list[dict[str, Any]] = []
        trace_samples: list[dict[str, Any]] = []

        for item in evidence:
            if item.get("status") != "ok":
                continue
            signal = item.get("signal", "")
            source = item.get("source", "")

            if source == "alertmanager":
                raw = item.get("raw") or []
                if isinstance(raw, list):
                    for alert in raw[:3]:
                        labels = alert.get("labels", {})
                        annotations = alert.get("annotations", {})
                        alerts.append({
                            "alertname": labels.get("alertname"),
                            "severity": labels.get("severity"),
                            "service": labels.get("service"),
                            "summary": annotations.get("summary", ""),
                            "startsAt": alert.get("startsAt"),
                        })
            elif signal == "logs":
                raw = item.get("raw") or {}
                if isinstance(raw, dict):
                    hits = (
                        raw.get("hits", {}).get("hits", [])
                        or raw.get("results", [])
                    )[:3]
                    for hit in hits:
                        body = self._log_body(hit)
                        if body:
                            log_samples.append({
                                "source": source,
                                "timestamp": self._log_timestamp(hit),
                                "severity": self._log_severity(hit),
                                "message": body[:300],
                            })
            elif signal == "metrics":
                raw = item.get("raw") or {}
                if isinstance(raw, dict):
                    results = raw.get("data", {}).get("result", [])
                    if results:
                        try:
                            metric_samples.append({
                                "source": source,
                                "query": item.get("query"),
                                "finding": item.get("finding"),
                                "value": results[0].get("value", [None, None])[1],
                            })
                        except Exception:
                            pass
            elif signal == "traces":
                trace_samples.append({
                    "source": source,
                    "finding": item.get("finding"),
                })

        return {
            "service": result.get("service"),
            "time_range": result.get("time_range"),
            "confidence": result.get("confidence"),
            "signal_coverage": result.get("signal_coverage", {}),
            "missing_signals": result.get("missing_signals", []),
            "root_cause": result.get("probable_root_cause", ""),
            "impact": result.get("impact", ""),
            "patterns": result.get("detected_patterns", []),
            "timeline": [
                {
                    "timestamp": t.get("timestamp"),
                    "source": t.get("source"),
                    "event": (t.get("event") or "")[:200],
                }
                for t in result.get("timeline", [])[:5]
            ],
            "active_alerts": alerts,
            "log_samples": log_samples[:5],
            "metric_samples": metric_samples[:5],
            "trace_samples": trace_samples[:3],
            "suggested_actions": result.get("suggested_actions", []),
        }

    # ------------------------------------------------------------------
    # Focused LLM analysis (strict evidence-bounded prompt)
    # ------------------------------------------------------------------

    def _generate_llm_analysis(
        self, ai_cfg: Any, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate a focused, evidence-bounded LLM analysis.

        The LLM is explicitly forbidden from inventing facts not present in
        the evidence.  Returns a dict matching LLMAnalysis schema.
        """
        try:
            from accelerators.ayosa.llm.analyst import AyosaAIAnalyst

            analyst = AyosaAIAnalyst({
                "provider": ai_cfg.provider or "anthropic",
                "api_key": ai_cfg.api_key,
                "model": ai_cfg.model,
                "azure_endpoint": ai_cfg.azure_endpoint,
                "azure_deployment": ai_cfg.azure_deployment,
                "openrouter_model": ai_cfg.openrouter_model,
            })
            context = self._generate_llm_context(result)
            return analyst.analyze_focused(context)
        except Exception as exc:
            logger.error("AYOSA LLM focused analysis failed: %s", exc, exc_info=True)
            return {
                "executive_summary": "LLM analysis was unavailable. See deterministic findings above.",
                "reasoning": f"LLM analysis failed: {exc}",
                "missing_information": [],
                "recommended_next_steps": [],
                "error": str(exc),
            }

    def _run_ai_analysis(self, ai_cfg: Any, investigation_result: dict[str, Any]) -> dict[str, Any]:
        """Call the configured LLM provider and return enriched analysis."""
        try:
            from accelerators.ayosa.llm.analyst import AyosaAIAnalyst

            analyst = AyosaAIAnalyst({
                "provider": ai_cfg.provider or "anthropic",
                "api_key": ai_cfg.api_key,
                "model": ai_cfg.model,
                "azure_endpoint": ai_cfg.azure_endpoint,
                "azure_deployment": ai_cfg.azure_deployment,
                "openrouter_model": ai_cfg.openrouter_model,
            })
            return analyst.analyze(investigation_result)
        except Exception as exc:
            logger.error("AYOSA AI analysis failed: %s", exc, exc_info=True)
            return {
                "error": str(exc),
                "narrative": f"AI analysis failed: {exc}",
                "executive_summary": "AI analysis was unavailable. See deterministic findings above.",
            }

    def _build_signal_coverage(self, tools) -> dict[str, list[str]]:
        coverage = {signal: [] for signal in EXPECTED_SIGNALS}

        for tool in tools:
            tool_name = tool.tool.lower().strip()
            capabilities = SIGNAL_CAPABILITIES.get(tool_name, [])

            for signal in capabilities:
                if signal in coverage and tool_name not in coverage[signal]:
                    coverage[signal].append(tool_name)

        return coverage

    def _missing_signals(self, coverage: dict[str, list[str]]) -> list[str]:
        return [
            signal
            for signal, providers in coverage.items()
            if not providers
        ]

    def _calculate_confidence(
        self,
        evidence: list[dict[str, Any]],
        missing_signals: list[str],
    ) -> float:
        ok_count = len([item for item in evidence if item.get("status") == "ok"])
        alert_count = len(self._extract_active_alerts(evidence))
        log_count = len(self._extract_log_hits(evidence))
        metric_count = len(self._extract_metric_values(evidence))

        confidence = 0.25

        if ok_count >= 2:
            confidence = 0.55
        if log_count > 0:
            confidence = max(confidence, 0.35)
        if metric_count > 0:
            confidence = 0.65
        if alert_count > 0 and log_count > 0:
            confidence = 0.78
        if alert_count > 0 and log_count > 0 and metric_count > 0:
            confidence = 0.84

        if "metrics" in missing_signals:
            confidence -= 0.08
        if "logs" in missing_signals:
            confidence -= 0.08
        if "alerts" in missing_signals:
            confidence -= 0.06
        if "traces" in missing_signals:
            confidence -= 0.03

        return max(0.15, round(confidence, 2))

    def _summarize_evidence(
        self,
        evidence: list[dict[str, Any]],
        service: str | None,
        time_range: str,
        ok_count: int,
        pending_count: int,
        error_count: int,
        signal_coverage: dict[str, list[str]],
        missing_signals: list[str],
    ) -> str:
        target = service or "the selected environment"

        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)
        metrics = self._extract_metric_values(evidence)

        summary_parts = [
            f"AYOSA investigated {target} over the last {time_range}.",
            f"It completed {ok_count} successful live checks, {pending_count} pending checks, and {error_count} failed checks.",
        ]

        coverage_sentence = self._summarize_signal_coverage(
            signal_coverage=signal_coverage,
            missing_signals=missing_signals,
        )
        if coverage_sentence:
            summary_parts.append(coverage_sentence)

        if alerts:
            critical_alerts = [
                alert for alert in alerts
                if alert.get("labels", {}).get("severity") == "critical"
            ]

            if critical_alerts:
                alert = critical_alerts[0]
                labels = alert.get("labels", {})
                annotations = alert.get("annotations", {})
                summary_parts.append(
                    "A critical active alert is present: "
                    f"{labels.get('alertname', 'unknown alert')} "
                    f"for service {labels.get('service', target)}. "
                    f"{annotations.get('summary', '')}".strip()
                )
            else:
                alert = alerts[0]
                labels = alert.get("labels", {})
                summary_parts.append(
                    "Active alerts were found, including "
                    f"{labels.get('alertname', 'unknown alert')}."
                )
        else:
            if "alerts" in missing_signals:
                summary_parts.append(
                    "Alert analysis was skipped because no alert-capable tool was provided."
                )
            else:
                summary_parts.append("No matching active alerts were found.")

        if metrics:
            metric_sentence = self._summarize_metrics(metrics)
            if metric_sentence:
                summary_parts.append(metric_sentence)
        elif "metrics" in missing_signals:
            summary_parts.append(
                "Metric analysis was skipped because no metrics-capable tool was provided."
            )

        if logs:
            log_sentence = self._summarize_logs(logs)
            if log_sentence:
                summary_parts.append(log_sentence)
        elif "logs" in missing_signals:
            summary_parts.append(
                "Log analysis was skipped because no logs-capable tool was provided."
            )

        if "traces" in missing_signals:
            summary_parts.append(
                "Trace analysis was skipped because no tracing-capable tool was provided."
            )

        probable_cause = self._infer_probable_cause(alerts, logs, metrics, service=service)
        summary_parts.append(f"Probable interpretation: {probable_cause}")

        return " ".join(summary_parts)

    def _summarize_signal_coverage(
        self,
        signal_coverage: dict[str, list[str]],
        missing_signals: list[str],
    ) -> str:
        available = [
            f"{signal} via {', '.join(providers)}"
            for signal, providers in signal_coverage.items()
            if providers
        ]

        parts = []

        if available:
            parts.append("Configured signal coverage: " + "; ".join(available) + ".")

        if missing_signals:
            parts.append(
                "Missing signal coverage: "
                + ", ".join(missing_signals)
                + ". AYOSA could not query these signal types because no matching validated tool was provided."
            )

        return " ".join(parts)

    def _extract_active_alerts(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        alerts = []

        for item in evidence:
            if item.get("source") != "alertmanager" or item.get("status") != "ok":
                continue

            raw = item.get("raw")
            if isinstance(raw, list):
                alerts.extend(raw)

        return alerts

    def _extract_log_hits(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hits = []

        for item in evidence:
            if item.get("signal") != "logs" or item.get("status") != "ok":
                continue

            raw = item.get("raw") or {}
            if not isinstance(raw, dict):
                continue

            # OpenSearch / Elasticsearch format
            search_hits = raw.get("hits", {}).get("hits", [])
            if search_hits:
                for hit in search_hits:
                    if isinstance(hit, dict):
                        hit["_ayosa_source"] = item.get("source")
                        hits.append(hit)

            # Splunk format from jobs/export adapter
            splunk_results = raw.get("results", [])
            if splunk_results:
                for hit in splunk_results:
                    if isinstance(hit, dict):
                        hit["_ayosa_source"] = item.get("source")
                        hits.append(hit)

        return hits

    def _extract_metric_values(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        metric_values = []

        for item in evidence:
            if item.get("signal") != "metrics" or item.get("status") != "ok":
                continue

            raw = item.get("raw") or {}
            result = (
                raw.get("data", {}).get("result", [])
                if isinstance(raw, dict)
                else []
            )

            if result:
                metric_values.append({
                    "finding": item.get("finding"),
                    "query": item.get("query"),
                    "source": item.get("source"),
                    "result": result,
                })

        return metric_values

    def _log_body(self, hit: dict[str, Any]) -> str:
        if not isinstance(hit, dict):
            return ""

        # OpenSearch / Elasticsearch
        source = hit.get("_source", {})
        if isinstance(source, dict) and source:
            return (
                source.get("body")
                or source.get("message")
                or source.get("log")
                or ""
            )

        # Splunk
        return (
            hit.get("_raw")
            or hit.get("body")
            or hit.get("message")
            or hit.get("event")
            or hit.get("log")
            or ""
        )

    def _log_timestamp(self, hit: dict[str, Any]) -> str | None:
        if not isinstance(hit, dict):
            return None

        source = hit.get("_source", {})
        if isinstance(source, dict) and source:
            return source.get("@timestamp") or source.get("observedTimestamp")

        return hit.get("_time") or hit.get("time") or hit.get("@timestamp")

    def _log_severity(self, hit: dict[str, Any]) -> str | None:
        if not isinstance(hit, dict):
            return None

        source = hit.get("_source", {})
        if isinstance(source, dict) and source:
            severity = source.get("severity")
            if isinstance(severity, dict):
                return severity.get("text")
            return severity

        return (
            hit.get("severity")
            or hit.get("otel.log.severity.text")
            or hit.get("level")
            or hit.get("log_level")
        )

    def _log_source(self, hit: dict[str, Any]) -> str:
        if not isinstance(hit, dict):
            return "logs"

        ayosa_source = hit.get("_ayosa_source")
        if ayosa_source:
            return ayosa_source

        if "_raw" in hit:
            return "splunk"

        source = hit.get("_source", {})
        if isinstance(source, dict):
            return source.get("source") or "logs"

        return "logs"

    def _summarize_metrics(self, metrics: list[dict[str, Any]]) -> str:
        readable = []

        for metric in metrics:
            finding = metric.get("finding", "")
            source = metric.get("source", "metrics")
            result = metric.get("result", [])

            if not result:
                continue

            try:
                value = result[0].get("value", [None, None])[1]
            except Exception:
                value = None

            if value is not None:
                readable.append(f"{source}: {finding} returned value {value}")

        if not readable:
            return ""

        return "Metric evidence: " + "; ".join(readable[:4]) + "."

    def _summarize_logs(self, logs: list[dict[str, Any]]) -> str:
        if not logs:
            return ""

        bodies = []
        sources = []

        for hit in logs[:10]:
            body = self._log_body(hit)
            if body:
                bodies.append(body.lower())

            source_name = self._log_source(hit)
            if source_name:
                sources.append(source_name)

        joined = " ".join(bodies)
        unique_sources = list(dict.fromkeys(sources))
        source_text = ", ".join(unique_sources) if unique_sources else "logs"

        patterns = []
        if "high memory usage" in joined:
            patterns.append("high memory usage")
        if "export timeout" in joined or "exporter export timeout" in joined:
            patterns.append("telemetry exporter timeout")
        if "kafka" in joined or "broker" in joined:
            patterns.append("Kafka or broker connectivity errors")
        if "broken pipe" in joined:
            patterns.append("broken pipe network errors")
        if "eof" in joined:
            patterns.append("EOF connection errors")
        if "unauthorized" in joined or "authentication" in joined:
            patterns.append("authentication or authorization errors")
        if "failed" in joined or "request failed" in joined:
            patterns.append("failed requests")
        if "invalid token" in joined:
            patterns.append("invalid token or authentication failures")

        if patterns:
            unique_patterns = list(dict.fromkeys(patterns))
            return (
                f"Log evidence from {source_text} found {len(logs)} matching events. "
                f"Common patterns include: {', '.join(unique_patterns)}."
            )

        return f"Log evidence from {source_text} found {len(logs)} matching events."

    def _infer_probable_cause(
        self,
        alerts: list[dict[str, Any]],
        logs: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
        service: str | None = None,
    ) -> str:
        target = service or "the service"
        log_text = " ".join(self._log_body(hit).lower() for hit in logs[:20])

        has_slo_alert      = any("slo" in str(a).lower() or "error budget" in str(a).lower() for a in alerts)
        has_memory         = "high memory usage" in log_text
        has_export_timeout = "export timeout" in log_text or "exporter export timeout" in log_text
        has_broker         = "kafka" in log_text or "broker" in log_text
        has_auth_failure   = "invalid token" in log_text or "unauthorized" in log_text or "auth" in log_text
        has_failed_req     = "request failed" in log_text or "connection refused" in log_text or "failed" in log_text

        if has_slo_alert and has_memory and has_export_timeout:
            return (
                f"{target} is under an active SLO burn condition. Logs show telemetry export failures "
                "caused by memory pressure, suggesting observability pipeline congestion may be obscuring "
                "the full incident scope. Correlate with collector and backend memory metrics."
            )

        if has_slo_alert and has_broker:
            return (
                f"{target} has an active SLO burn alert and logs show messaging broker errors. "
                "The degradation is likely caused by upstream or downstream messaging instability."
            )

        if has_slo_alert:
            return (
                f"{target} has an active SLO burn alert. "
                "Inspect the alert's generator query and related service logs to identify the burning window."
            )

        if has_memory or has_export_timeout:
            return (
                "Logs indicate telemetry export or memory pressure issues. "
                "Check the OpenTelemetry Collector, backend storage, and container memory limits."
            )

        if has_broker:
            return (
                f"Logs indicate messaging broker connectivity issues affecting {target}. "
                "Check broker health and producer/consumer connectivity."
            )

        if has_auth_failure:
            return (
                f"Logs show repeated authentication or token failures for {target}. "
                "Review credential validity, token expiry, and recent auth configuration changes."
            )

        if has_failed_req:
            return (
                f"Logs show repeated failed requests for {target}. "
                "Review request validation, dependency availability, and recent configuration changes."
            )

        if logs:
            return (
                "Log evidence is available but no strong known incident pattern was matched. "
                "Review the newest log events and expand correlation with metrics, alerts, and traces."
            )

        if metrics:
            return (
                "Metrics are available but no strong incident pattern was inferred. "
                "Review latency, request-rate, and error-rate trends for anomalies."
            )

        return (
            "Limited evidence found. Expand the time range or include additional observability tools "
            "covering metrics, logs, alerts, and traces."
        )


    def _suggest_actions(
        self,
        evidence: list[dict[str, Any]],
        missing_signals: list[str],
    ) -> list[str]:
        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)

        actions = []

        if alerts:
            actions.append("Open the active alert and inspect its generator query.")
            actions.append("Check the SLO burn-rate windows and identify when the burn started.")

        if "alerts" in missing_signals:
            actions.append("Add an alert-capable tool such as Alertmanager, Splunk, Grafana, Datadog, or Dynatrace to inspect active alerts.")

        if "metrics" in missing_signals:
            actions.append("Add a metrics-capable tool such as Prometheus, Datadog, Dynatrace, or AppDynamics to inspect latency, traffic, and error-rate trends.")

        if "logs" in missing_signals:
            actions.append("Add a logs-capable tool such as OpenSearch, Elasticsearch, Splunk, Loki, Datadog, or Dynatrace to inspect error events.")

        if "traces" in missing_signals:
            actions.append("Add a tracing-capable tool such as Jaeger, Tempo, Datadog, Dynatrace, or AppDynamics to inspect slow or failing spans.")

        log_text = " ".join(
            self._log_body(hit).lower()
            for hit in logs[:20]
        )

        if "high memory usage" in log_text or "export timeout" in log_text:
            actions.append("Check OpenTelemetry Collector and backend memory usage; look for refused exports.")
            actions.append("Reduce telemetry load or increase memory limits if collector/exporter pressure is confirmed.")

        if "kafka" in log_text or "broker" in log_text:
            actions.append("Check messaging broker health and producer/consumer connectivity.")

        actions.extend([
            "Compare the alert timestamp with matching log and metric events where available.",
            "Use the confirmed evidence to generate or export a runbook.",
        ])

        return list(dict.fromkeys(actions))

    def _summarize_impact(self, evidence: list[dict[str, Any]], service: str | None) -> str:
        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)

        target = service or "the selected service"

        critical_alerts = [
            alert for alert in alerts
            if alert.get("labels", {}).get("severity") == "critical"
        ]

        if critical_alerts:
            alert = critical_alerts[0]
            labels = alert.get("labels", {})
            slo = labels.get("sloth_slo") or labels.get("slo")
            journey = labels.get("journey")

            if slo or journey:
                return (
                    f"{target} is impacted by a critical SLO/error-budget alert"
                    f"{f' for journey {journey}' if journey else ''}"
                    f"{f' and SLO {slo}' if slo else ''}."
                )

            return f"{target} has at least one critical active alert."

        if logs:
            return f"{target} has matching error-like log events, but no critical active alert was found."

        return f"No clear user-facing impact was detected for {target} from the available signals."

    def _detect_patterns(self, evidence: list[dict[str, Any]]) -> list[str]:
        patterns = []

        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)
        metrics = self._extract_metric_values(evidence)

        if alerts:
            patterns.append("active_alerts_present")

        if any(
            alert.get("labels", {}).get("severity") == "critical"
            for alert in alerts
        ):
            patterns.append("critical_alert_present")

        if any(
            "slo" in str(alert).lower() or "error budget" in str(alert).lower()
            for alert in alerts
        ):
            patterns.append("slo_error_budget_burn")

        log_text = " ".join(
            self._log_body(hit).lower()
            for hit in logs[:20]
        )

        if "failed" in log_text or "request failed" in log_text:
            patterns.append("failed_requests")

        if "invalid token" in log_text:
            patterns.append("invalid_token_auth_failure")

        if "high memory usage" in log_text:
            patterns.append("high_memory_usage")

        if "export timeout" in log_text or "exporter export timeout" in log_text:
            patterns.append("telemetry_export_timeout")

        if "kafka" in log_text or "broker" in log_text:
            patterns.append("kafka_or_broker_errors")

        if "broken pipe" in log_text:
            patterns.append("network_broken_pipe")

        if "eof" in log_text:
            patterns.append("connection_eof")

        if metrics:
            patterns.append("metrics_available")

        return list(dict.fromkeys(patterns))

    def _build_timeline(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        timeline = []

        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)

        for alert in alerts[:5]:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            timeline.append({
                "timestamp": alert.get("startsAt"),
                "source": "alertmanager",
                "event": (
                    f"Alert started: {labels.get('alertname', 'unknown alert')}. "
                    f"{annotations.get('summary', '')}".strip()
                ),
                "severity": labels.get("severity"),
            })

        for hit in logs[:5]:
            body = self._log_body(hit) or "log event"

            timeline.append({
                "timestamp": self._log_timestamp(hit),
                "source": self._log_source(hit),
                "event": body[:240],
                "severity": self._log_severity(hit),
            })

        timeline = sorted(
            timeline,
            key=lambda item: item.get("timestamp") or "",
            reverse=True,
        )

        return timeline[:10]

    def _related_artifacts(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        artifacts = []

        for alert in self._extract_active_alerts(evidence):
            labels = alert.get("labels", {})
            generator_url = alert.get("generatorURL")

            artifacts.append({
                "type": "alert",
                "name": labels.get("alertname", "Alert"),
                "source": "alerts",
                "url": generator_url,
                "metadata": labels,
            })

        for item in evidence:
            if item.get("query"):
                artifacts.append({
                    "type": "query",
                    "name": item.get("finding", "Observability query"),
                    "source": item.get("source"),
                    "query": item.get("query"),
                })

        return artifacts[:20]

    def generate_runbook(self, result: dict[str, Any]) -> str:
        service = result.get("service") or "unknown-service"

        impact = result.get("impact", "")
        root_cause = result.get("probable_root_cause", "")

        patterns = result.get("detected_patterns", [])
        actions = result.get("suggested_actions", [])
        timeline = result.get("timeline", [])
        signal_coverage = result.get("signal_coverage", {})
        missing_signals = result.get("missing_signals", [])

        lines: list[str] = []

        lines.append(f"# AYOSA Incident Runbook — {service}")
        lines.append("")
        lines.append("## Incident Summary")
        lines.append(result.get("answer", ""))
        lines.append("")

        lines.append("## Signal Coverage")
        if signal_coverage:
            for signal, providers in signal_coverage.items():
                provider_text = ", ".join(providers) if providers else "not available"
                lines.append(f"- {signal}: {provider_text}")
        if missing_signals:
            lines.append("")
            lines.append("Missing signals:")
            for signal in missing_signals:
                lines.append(f"- {signal}")
        lines.append("")

        lines.append("## Impact")
        lines.append(impact)
        lines.append("")

        lines.append("## Probable Root Cause")
        lines.append(root_cause)
        lines.append("")

        if patterns:
            lines.append("## Detected Signal Patterns")
            for pattern in patterns:
                lines.append(f"- {pattern}")
            lines.append("")

        if timeline:
            lines.append("## Timeline")
            for item in timeline[:10]:
                timestamp = item.get("timestamp", "unknown-time")
                event = item.get("event", "")
                source = item.get("source", "unknown")
                lines.append(f"- [{timestamp}] ({source}) {event}")
            lines.append("")

        if actions:
            lines.append("## Recommended Actions")
            for index, action in enumerate(actions, start=1):
                lines.append(f"{index}. {action}")
            lines.append("")

        lines.append("## Validation Checklist")
        lines.append("- Verify alert clears after remediation, if alert data is available.")
        lines.append("- Confirm latency, request-rate, and error-rate normalize, if metrics data is available.")
        lines.append("- Confirm log error frequency decreases, if log data is available.")
        lines.append("- Validate downstream dependency stability.")
        lines.append("- Capture post-incident learnings.")
        lines.append("")

        return "\n".join(lines)