"""Base adapter class."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class AdapterError(Exception):
    """Raised when an adapter fails to extract data."""


class BaseAdapter:
    """Common functionality for all adapters."""

    tool_name: str = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.url = config.get("url", "").rstrip("/")
        self.timeout = config.get("timeout", 30)
        self.verify_tls = config.get("verify_tls", True)
        self.session = requests.Session()
        self._configure_auth()

    def _configure_auth(self) -> None:
        """Override in subclasses to set auth headers/credentials."""
        username = self.config.get("username")
        password = self.config.get("password")
        if username and password:
            self.session.auth = (username, password)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET a JSON endpoint."""
        full_url = f"{self.url}{path}"
        try:
            resp = self.session.get(
                full_url,
                params=params,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.warning("GET %s failed: %s", full_url, e)
            raise AdapterError(f"{self.tool_name} request failed: {e}") from e

    def health_check(self) -> bool:
        """Return True if the tool is reachable."""
        raise NotImplementedError

    def extract(self) -> dict[str, Any]:
        """Extract all relevant data from the tool."""
        raise NotImplementedError
