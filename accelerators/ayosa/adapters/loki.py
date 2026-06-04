import requests

from accelerators.ayosa.adapters._llm_query import (
    extract_llm_query,
    extract_llm_reason,
)


class LokiAdapter:
    signal = "logs"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        headers = {}
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

        if service:
            query = f'{{service_name="{service}"}} |= "error"'
        else:
            query = '{job=~".+"} |= "error"'

        try:
            response = requests.get(
                f"{self.base_url}/loki/api/v1/query",
                headers=self._headers(),
                params={"query": query},
                timeout=10,
            )
            response.raise_for_status()

            return llm_evidence + [{
                "source": "loki",
                "signal": "logs",
                "finding": "Retrieved recent error logs from Loki.",
                "query": query,
                "status": "ok",
                "raw": response.json(),
            }]

        except Exception as exc:
            return llm_evidence + [{
                "source": "loki",
                "signal": "logs",
                "finding": f"Loki query failed: {exc}",
                "query": query,
                "status": "error",
                "raw": None,
            }]

    # ------------------------------------------------------------------ #
    # Step 12(a): consume an LLM-supplied LogQL string verbatim.
    # ------------------------------------------------------------------ #
    def _run_llm_query(self, plan: dict | None) -> list[dict]:
        llm_query = extract_llm_query(plan)
        if not llm_query:
            return []
        reason = extract_llm_reason(plan)
        try:
            response = requests.get(
                f"{self.base_url}/loki/api/v1/query_range",
                headers=self._headers(),
                params={"query": llm_query, "limit": 100},
                timeout=10,
            )
            response.raise_for_status()
            return [{
                "source": "loki",
                "signal": "logs",
                "finding": f"LLM-selected Loki query executed: {reason}".strip(": "),
                "query": llm_query,
                "status": "ok",
                "raw": response.json(),
            }]
        except Exception as exc:
            return [{
                "source": "loki",
                "signal": "logs",
                "finding": f"LLM-selected Loki query failed: {exc}",
                "query": llm_query,
                "status": "error",
                "raw": None,
            }]