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

        confidence = 0.25
        if ok_count >= 1:
            confidence = 0.45
        if ok_count >= 2:
            confidence = 0.65
        if ok_count >= 3:
            confidence = 0.78

        return {
            "answer": (
                f"AYOSA investigated live observability data using {len(request.tools)} configured tools. "
                f"Successful checks: {ok_count}. Pending adapters: {pending_count}. Failed checks: {error_count}. "
                "Review the evidence section for exact tool responses and queries."
            ),
            "service": request.service,
            "time_range": request.time_range,
            "confidence": confidence,
            "evidence": evidence,
            "suggested_actions": [
                "Review active alerts first.",
                "Check Prometheus latency, request-rate, and target-health results.",
                "Open Jaeger traces for slow service paths.",
                "Correlate log errors from Splunk or Elasticsearch/OpenSearch in the same time window.",
                "Use confirmed evidence to generate a runbook in the next phase.",
            ],
        }