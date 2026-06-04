import requests

from accelerators.ayosa.adapters._llm_query import (
    extract_llm_query,
    extract_llm_reason,
)


class DatadogAdapter:
    signal = "observability"

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

        if not self.auth_token:
            return llm_evidence + [{
                "source": "datadog",
                "signal": "observability",
                "finding": "Datadog API token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        return llm_evidence + [{
            "source": "datadog",
            "signal": "observability",
            "finding": "Datadog adapter is registered. Detailed metrics/logs/traces API integration will be added in the Datadog-specific phase.",
            "query": None,
            "status": "not_implemented",
            "raw": None,
        }]

    # ------------------------------------------------------------------ #
    # Step 12(a): surface the LLM-supplied query as a skipped row until
    # the full Datadog HTTP integration lands.
    # ------------------------------------------------------------------ #
    def _run_llm_query(self, plan: dict | None) -> list[dict]:
        llm_query = extract_llm_query(plan)
        if not llm_query:
            return []
        reason = extract_llm_reason(plan)
        return [{
            "source": "datadog",
            "signal": "observability",
            "finding": (
                f"LLM-selected Datadog query skipped (adapter not yet wired): {reason}".strip(": ")
            ),
            "query": llm_query,
            "status": "skipped",
            "raw": None,
        }]