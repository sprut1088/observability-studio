import requests

from accelerators.ayosa.adapters._llm_query import (
    extract_llm_query,
    extract_llm_reason,
)


class GrafanaAdapter:
    signal = "dashboards"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def investigate(
        self,
        service: str | None,
        time_range: str,
        message: str,
        plan: dict | None = None,
    ):
        llm_evidence = self._run_llm_query(plan)
        query = service or message

        try:
            response = requests.get(
                f"{self.base_url}/api/search",
                headers=self._headers(),
                params={"query": query},
                timeout=10,
            )
            response.raise_for_status()

            dashboards = response.json()

            return llm_evidence + [{
                "source": "grafana",
                "signal": "dashboards",
                "finding": f"Retrieved {len(dashboards)} matching Grafana dashboards or folders.",
                "query": f"/api/search?query={query}",
                "status": "ok",
                "raw": dashboards,
            }]

        except Exception as exc:
            return llm_evidence + [{
                "source": "grafana",
                "signal": "dashboards",
                "finding": f"Grafana query failed: {exc}",
                "query": f"/api/search?query={query}",
                "status": "error",
                "raw": None,
            }]

    # ------------------------------------------------------------------ #
    # Step 12(a): consume an LLM-supplied dashboard search term.
    # ------------------------------------------------------------------ #
    def _run_llm_query(self, plan: dict | None) -> list[dict]:
        llm_query = extract_llm_query(plan)
        if not llm_query:
            return []
        reason = extract_llm_reason(plan)
        try:
            response = requests.get(
                f"{self.base_url}/api/search",
                headers=self._headers(),
                params={"query": llm_query},
                timeout=10,
            )
            response.raise_for_status()
            dashboards = response.json()
            return [{
                "source": "grafana",
                "signal": "dashboards",
                "finding": f"LLM-selected Grafana query executed: {reason}".strip(": "),
                "query": llm_query,
                "status": "ok",
                "raw": dashboards,
            }]
        except Exception as exc:
            return [{
                "source": "grafana",
                "signal": "dashboards",
                "finding": f"LLM-selected Grafana query failed: {exc}",
                "query": llm_query,
                "status": "error",
                "raw": None,
            }]