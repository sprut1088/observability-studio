"""Base adapter class."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# HTTP status codes that warrant a retry
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 1.0   # seconds; doubles each retry (1s, 2s, 4s)


class AdapterError(Exception):
    """Raised when an adapter fails to extract data."""


class BaseAdapter:
    """Common functionality for all adapters."""

    tool_name: str = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.url = config.get("url", "").rstrip("/")
        # Default 45 s — generous enough for slow proxies, short enough not to hang
        self.timeout = config.get("timeout", 45)
        # Default False: lab / demo environments routinely use self-signed certs
        self.verify_tls = config.get("verify_tls", False)
        self.session = requests.Session()

        # Suppress InsecureRequestWarning when verify_tls is False
        if not self.verify_tls:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self._configure_auth()

    def _configure_auth(self) -> None:
        """Override in subclasses to set auth headers/credentials."""
        username = self.config.get("username")
        password = self.config.get("password")
        if username and password:
            self.session.auth = (username, password)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET a JSON endpoint with retry/backoff on transient errors."""
        full_url = f"{self.url}{path}"
        last_exc: Exception | None = None

        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = self.session.get(
                    full_url,
                    params=params,
                    timeout=self.timeout,
                    verify=self.verify_tls,
                )
                if resp.status_code in _RETRY_STATUSES and attempt < _RETRY_ATTEMPTS - 1:
                    wait = _RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "GET %s → HTTP %s (attempt %d/%d), retrying in %.0fs …",
                        full_url, resp.status_code, attempt + 1, _RETRY_ATTEMPTS, wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.ConnectionError as e:
                last_exc = e
                if attempt < _RETRY_ATTEMPTS - 1:
                    wait = _RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "GET %s → connection error (attempt %d/%d), retrying in %.0fs …",
                        full_url, attempt + 1, _RETRY_ATTEMPTS, wait,
                    )
                    time.sleep(wait)
            except requests.exceptions.Timeout as e:
                last_exc = e
                if attempt < _RETRY_ATTEMPTS - 1:
                    wait = _RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "GET %s → timeout after %ss (attempt %d/%d), retrying in %.0fs …",
                        full_url, self.timeout, attempt + 1, _RETRY_ATTEMPTS, wait,
                    )
                    time.sleep(wait)
            except requests.exceptions.RequestException as e:
                # Non-retryable (4xx, etc.)
                logger.warning("GET %s failed: %s", full_url, e)
                raise AdapterError(f"{self.tool_name} request failed: {e}") from e

        # All retries exhausted
        logger.error("GET %s failed after %d attempts: %s", full_url, _RETRY_ATTEMPTS, last_exc)
        raise AdapterError(
            f"{self.tool_name} request failed after {_RETRY_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    def health_check(self) -> bool:
        """Return True if the tool is reachable."""
        raise NotImplementedError

    def extract(self) -> dict[str, Any]:
        """Extract all relevant data from the tool."""
        raise NotImplementedError
