from typing import Any

from accelerators.ayosa.registry import ADAPTERS


class AyosaService:
    def investigate(self, request):
        evidence = []

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

        confidence = self._calculate_confidence(evidence)
        answer = self._summarize_evidence(
            evidence=evidence,
            service=request.service,
            time_range=request.time_range,
            ok_count=ok_count,
            pending_count=pending_count,
            error_count=error_count,
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
            "probable_root_cause": probable_root_cause,
            "impact": self._summarize_impact(evidence, request.service),
            "detected_patterns": self._detect_patterns(evidence),
            "timeline": self._build_timeline(evidence),
            "related_artifacts": self._related_artifacts(evidence),
            "evidence": evidence,
            "suggested_actions": self._suggest_actions(evidence),
        }

    def _calculate_confidence(self, evidence: list[dict[str, Any]]) -> float:
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

        return confidence

    def _summarize_evidence(
        self,
        evidence: list[dict[str, Any]],
        service: str | None,
        time_range: str,
        ok_count: int,
        pending_count: int,
        error_count: int,
    ) -> str:
        target = service or "the selected environment"

        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)
        metrics = self._extract_metric_values(evidence)

        summary_parts = [
            f"AYOSA investigated {target} over the last {time_range}.",
            f"It completed {ok_count} successful live checks, {pending_count} pending checks, and {error_count} failed checks.",
        ]

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
            summary_parts.append("No matching active alerts were found.")

        if metrics:
            metric_sentence = self._summarize_metrics(metrics)
            if metric_sentence:
                summary_parts.append(metric_sentence)

        if logs:
            log_sentence = self._summarize_logs(logs)
            if log_sentence:
                summary_parts.append(log_sentence)

        probable_cause = self._infer_probable_cause(alerts, logs, metrics)
        summary_parts.append(f"Probable interpretation: {probable_cause}")

        return " ".join(summary_parts)

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
                raw.get("hits", {})
                .get("hits", [])
                if isinstance(raw, dict)
                else []
            )
            hits.extend(search_hits)

        return hits

    def _extract_metric_values(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        metric_values = []

        for item in evidence:
            if item.get("source") != "prometheus" or item.get("status") != "ok":
                continue

            raw = item.get("raw") or {}
            result = (
                raw.get("data", {})
                .get("result", [])
                if isinstance(raw, dict)
                else []
            )

            if result:
                metric_values.append({
                    "finding": item.get("finding"),
                    "query": item.get("query"),
                    "result": result,
                })

        return metric_values

    def _summarize_metrics(self, metrics: list[dict[str, Any]]) -> str:
        readable = []

        for metric in metrics:
            finding = metric.get("finding", "")
            result = metric.get("result", [])

            if not result:
                continue

            try:
                value = result[0].get("value", [None, None])[1]
            except Exception:
                value = None

            if value is not None:
                readable.append(f"{finding} returned value {value}")

        if not readable:
            return ""

        return "Prometheus metric evidence: " + "; ".join(readable[:4]) + "."

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
                f"OpenSearch log evidence found {len(logs)} matching events. "
                f"Common patterns include: {', '.join(unique_patterns)}."
            )

        return f"OpenSearch log evidence found {len(logs)} matching events."

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
            "AYOSA found limited evidence. Expand the time range or include more tools such as traces, logs, and alerts."
        )

    def _suggest_actions(self, evidence: list[dict[str, Any]]) -> list[str]:
        alerts = self._extract_active_alerts(evidence)
        logs = self._extract_log_hits(evidence)

        actions = []

        if alerts:
            actions.append("Open the active Alertmanager alert and inspect its Prometheus generator query.")
            actions.append("Check the SLO burn-rate windows and identify when the burn started.")

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
            "Compare the alert timestamp with matching OpenSearch log events.",
            "Use the confirmed evidence to generate a runbook in the next AYOSA phase.",
        ])

        return actions
    

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

            timeline.append({
                "timestamp": source.get("@timestamp") or source.get("observedTimestamp"),
                "source": "opensearch",
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
                "name": labels.get("alertname", "Alertmanager alert"),
                "source": "alertmanager",
                "url": generator_url,
                "metadata": labels,
            })

        for item in evidence:
            if item.get("source") == "prometheus" and item.get("query"):
                artifacts.append({
                    "type": "query",
                    "name": item.get("finding", "Prometheus query"),
                    "source": "prometheus",
                    "query": item.get("query"),
                })

            if item.get("source") == "elasticsearch" and item.get("query"):
                artifacts.append({
                    "type": "query",
                    "name": "OpenSearch log query",
                    "source": "opensearch",
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

        lines: list[str] = []

        lines.append(f"# AYOSA Incident Runbook — {service}")
        lines.append("")
        lines.append("## Incident Summary")
        lines.append(result.get("answer", ""))
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
            for action in actions:
                lines.append(f"1. {action}")
            lines.append("")

        lines.append("## Validation Checklist")
        lines.append("- Verify alert clears after remediation.")
        lines.append("- Confirm Prometheus latency and request-rate normalize.")
        lines.append("- Confirm OpenSearch error frequency decreases.")
        lines.append("- Validate downstream Kafka/export pipeline stability.")
        lines.append("- Capture post-incident learnings.")
        lines.append("")

        return "\n".join(lines)
