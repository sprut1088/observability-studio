from typing import Any

from accelerators.ayosa.registry import ADAPTERS

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
    def investigate(self, request):
        evidence = []

        signal_coverage = self._build_signal_coverage(request.tools)
        missing_signals = self._missing_signals(signal_coverage)

        for tool in request.tools:
            tool_key = tool.tool.lower().strip()
            adapter_cls = ADAPTERS.get(tool_key)

            if not adapter_cls:
                evidence.append({
                    "source": tool.tool,
                    "signal": "unknown",
                    "finding": f"No AYOSA adapter found for tool: {tool.tool}",
                    "query": None,
                    "status": "error",
                    "raw": None,
                })
                continue

            adapter = adapter_cls(
                base_url=tool.base_url,
                auth_token=tool.auth_token,
            )

            try:
                evidence.extend(
                    adapter.investigate(
                        service=request.service,
                        time_range=request.time_range,
                        message=request.message,
                    )
                )
            except Exception as exc:
                evidence.append({
                    "source": tool.tool,
                    "signal": "unknown",
                    "finding": f"AYOSA adapter execution failed for {tool.tool}: {exc}",
                    "query": None,
                    "status": "error",
                    "raw": None,
                })

        ok_count = len([item for item in evidence if item.get("status") == "ok"])
        pending_count = len([item for item in evidence if item.get("status") == "not_implemented"])
        error_count = len([item for item in evidence if item.get("status") == "error"])

        confidence = self._calculate_confidence(evidence, missing_signals)

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

        probable_root_cause = self._infer_probable_cause(
            self._extract_active_alerts(evidence),
            self._extract_log_hits(evidence),
            self._extract_metric_values(evidence),
        )

        return {
            "answer": answer,
            "service": request.service,
            "time_range": request.time_range,
            "confidence": confidence,
            "signal_coverage": signal_coverage,
            "missing_signals": missing_signals,
            "probable_root_cause": probable_root_cause,
            "impact": self._summarize_impact(evidence, request.service),
            "detected_patterns": self._detect_patterns(evidence),
            "timeline": self._build_timeline(evidence),
            "related_artifacts": self._related_artifacts(evidence),
            "evidence": evidence,
            "suggested_actions": self._suggest_actions(evidence, missing_signals),
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

        probable_cause = self._infer_probable_cause(alerts, logs, metrics)
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
            search_hits = (
                raw.get("hits", {}).get("hits", [])
                if isinstance(raw, dict)
                else []
            )
            hits.extend(search_hits)

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
        for hit in logs[:5]:
            source = hit.get("_source", {})
            body = source.get("body") or source.get("message") or ""
            if body:
                bodies.append(body.lower())

        joined = " ".join(bodies)

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

        if patterns:
            unique_patterns = list(dict.fromkeys(patterns))
            return (
                f"Log evidence found {len(logs)} matching events. "
                f"Common patterns include: {', '.join(unique_patterns)}."
            )

        return f"Log evidence found {len(logs)} matching events."

    def _infer_probable_cause(
        self,
        alerts: list[dict[str, Any]],
        logs: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
    ) -> str:
        log_text = " ".join(
            str(hit.get("_source", {}).get("body", "")).lower()
            for hit in logs[:10]
        )

        has_slo_alert = any(
            "slo" in str(alert).lower()
            or "error budget" in str(alert).lower()
            for alert in alerts
        )

        has_memory = "high memory usage" in log_text
        has_export_timeout = "export timeout" in log_text or "exporter export timeout" in log_text
        has_kafka = "kafka" in log_text or "broker" in log_text

        if has_slo_alert and has_memory and has_export_timeout:
            return (
                "checkout is under an active SLO burn condition, while logs show telemetry export failures "
                "caused by high memory usage. This suggests observability pipeline pressure may be contributing "
                "to missing or delayed signals, and the checkout incident should be correlated with collector or backend memory saturation."
            )

        if has_slo_alert and has_kafka:
            return (
                "checkout has an active SLO burn alert and logs show Kafka or broker connectivity errors. "
                "This suggests the checkout path may be affected by messaging instability or downstream dependency issues."
            )

        if has_slo_alert:
            return (
                "checkout has an active SLO burn alert. Prioritize the alert's generator query and related service logs."
            )

        if has_memory or has_export_timeout:
            return (
                "logs indicate telemetry export or memory pressure problems. Check OpenTelemetry Collector, backend storage, and container memory limits."
            )

        if has_kafka:
            return (
                "logs indicate Kafka or broker connectivity issues. Check Kafka health and checkout producer or metadata connectivity."
            )

        if metrics:
            return (
                "metrics are available, but no strong incident pattern was inferred yet. Review latency, request-rate, and error-rate trends."
            )

        return (
            "AYOSA found limited evidence. Expand the time range or include more tools such as metrics, logs, alerts, and traces."
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
            str(hit.get("_source", {}).get("body", "")).lower()
            for hit in logs[:10]
        )

        if "high memory usage" in log_text or "export timeout" in log_text:
            actions.append("Check OpenTelemetry Collector and backend memory usage; look for refused exports.")
            actions.append("Reduce telemetry load or increase memory limits if collector/exporter pressure is confirmed.")

        if "kafka" in log_text or "broker" in log_text:
            actions.append("Check Kafka container health and checkout-to-Kafka connectivity.")

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
            str(hit.get("_source", {}).get("body", "")).lower()
            for hit in logs[:20]
        )

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
            source = hit.get("_source", {})
            body = source.get("body") or source.get("message") or "log event"
            severity = source.get("severity", {})
            if isinstance(severity, dict):
                severity = severity.get("text")

            source_name = source.get("source") or "logs"

            timeline.append({
                "timestamp": source.get("@timestamp") or source.get("observedTimestamp"),
                "source": source_name,
                "event": body[:240],
                "severity": severity,
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