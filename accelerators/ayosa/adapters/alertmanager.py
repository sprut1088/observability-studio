import requests

from accelerators.ayosa.adapters._llm_query import (
    extract_llm_query,
    extract_llm_reason,
)


class AlertmanagerAdapter:
    signal = "alerts"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def investigate(
        self,
        service: str | None,
        time_range: str,
        message: str,
        plan: dict | None = None,
    ):
        llm_evidence = self._run_llm_query(plan)

        try:
            response = requests.get(
                f"{self.base_url}/api/v2/alerts",
                timeout=10,
            )
            response.raise_for_status()

            alerts = response.json()

            if service:
                alerts = [
                    alert for alert in alerts
                    if service in str(alert)
                ]

            return llm_evidence + [{
                "source": "alertmanager",
                "signal": "alerts",
                "finding": f"Retrieved {len(alerts)} matching active alerts.",
                "query": "/api/v2/alerts",
                "status": "ok",
                "raw": alerts,
            }]
        except Exception as exc:
            return llm_evidence + [{
                "source": "alertmanager",
                "signal": "alerts",
                "finding": f"Alertmanager query failed: {exc}",
                "query": "/api/v2/alerts",
                "status": "error",
                "raw": None,
            }]

    # ------------------------------------------------------------------ #
    # Step 12(a): consume an LLM-supplied Alertmanager matcher string.
    # Each non-empty line is sent as a separate ``filter`` repeat.
    # ------------------------------------------------------------------ #
    def _run_llm_query(self, plan: dict | None) -> list[dict]:
        llm_query = extract_llm_query(plan)
        if not llm_query:
            return []
        reason = extract_llm_reason(plan)
        filters = [line.strip() for line in llm_query.splitlines() if line.strip()] or [llm_query]
        try:
            response = requests.get(
                f"{self.base_url}/api/v2/alerts",
                params=[("filter", f) for f in filters],
                timeout=10,
            )
            response.raise_for_status()
            alerts = response.json()
            return [{
                "source": "alertmanager",
                "signal": "alerts",
                "finding": f"LLM-selected Alertmanager query executed: {reason}".strip(": "),
                "query": llm_query,
                "status": "ok",
                "raw": alerts,
            }]
        except Exception as exc:
            return [{
                "source": "alertmanager",
                "signal": "alerts",
                "finding": f"LLM-selected Alertmanager query failed: {exc}",
                "query": llm_query,
                "status": "error",
                "raw": None,
            }]