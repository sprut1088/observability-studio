import requests

from accelerators.ayosa.adapters._llm_query import (
    extract_llm_query,
    extract_llm_reason,
)


class DynatraceAdapter:
    signal = "observability"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        if self.auth_token:
            return {"Authorization": f"Api-Token {self.auth_token}"}
        return {}

    def investigate(
        self,
        service: str | None,
        time_range: str,
        message: str,
        plan: dict | None = None,
    ):
        llm_evidence = self._run_llm_query(plan)

        if not self.auth_token:
            return llm_evidence + [{
                "source": "dynatrace",
                "signal": "observability",
                "finding": "Dynatrace API token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        try:
            response = requests.get(
                f"{self.base_url}/api/v2/problems",
                headers=self._headers(),
                timeout=10,
            )
            response.raise_for_status()

            return llm_evidence + [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": "Retrieved current Dynatrace problems.",
                "query": "/api/v2/problems",
                "status": "ok",
                "raw": response.json(),
            }]

        except Exception as exc:
            return llm_evidence + [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": f"Dynatrace query failed: {exc}",
                "query": "/api/v2/problems",
                "status": "error",
                "raw": None,
            }]

    # ------------------------------------------------------------------ #
    # Step 12(a): consume an LLM-supplied ``problemSelector`` string.
    # Skipped (not error) when no auth token is configured.
    # ------------------------------------------------------------------ #
    def _run_llm_query(self, plan: dict | None) -> list[dict]:
        llm_query = extract_llm_query(plan)
        if not llm_query:
            return []
        reason = extract_llm_reason(plan)
        if not self.auth_token:
            return [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": "LLM-selected Dynatrace query skipped: missing API token.",
                "query": llm_query,
                "status": "skipped",
                "raw": None,
            }]
        try:
            response = requests.get(
                f"{self.base_url}/api/v2/problems",
                headers=self._headers(),
                params={"problemSelector": llm_query},
                timeout=10,
            )
            response.raise_for_status()
            return [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": f"LLM-selected Dynatrace query executed: {reason}".strip(": "),
                "query": llm_query,
                "status": "ok",
                "raw": response.json(),
            }]
        except Exception as exc:
            return [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": f"LLM-selected Dynatrace query failed: {exc}",
                "query": llm_query,
                "status": "error",
                "raw": None,
            }]