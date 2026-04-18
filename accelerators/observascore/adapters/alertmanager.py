"""AlertManager adapter - read-only extraction via HTTP API."""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import AlertReceiver

logger = logging.getLogger(__name__)

# Receiver types we recognise and flag as integrations
_KNOWN_INTEGRATIONS = {
    "pagerduty_configs": "pagerduty",
    "opsgenie_configs": "opsgenie",
    "slack_configs": "slack",
    "email_configs": "email",
    "webhook_configs": "webhook",
    "victorops_configs": "victorops",
    "msteams_configs": "msteams",
    "sns_configs": "sns",
    "wechat_configs": "wechat",
    "telegram_configs": "telegram",
}


class AlertManagerAdapter(BaseAdapter):
    tool_name = "alertmanager"

    def health_check(self) -> bool:
        try:
            data = self._get("/-/healthy")
            return True
        except Exception:
            try:
                self._get("/api/v2/status")
                return True
            except Exception as e:
                logger.error("AlertManager health check failed: %s", e)
                return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from AlertManager at %s", self.url)
        result: dict[str, Any] = {
            "receivers": [],
            "silences": [],
            "active_alerts_count": 0,
            "inhibition_rules_count": 0,
            "integrations": [],
            "errors": [],
        }

        # Status (contains config summary with receivers and inhibition rules)
        try:
            status = self._get("/api/v2/status")
            config = status.get("config", {}) or {}
            original_config = config.get("original", "")

            # Parse receivers from the status endpoint
            raw_receivers = status.get("config", {})
            # Also try the dedicated receivers endpoint
        except AdapterError as e:
            result["errors"].append(f"status: {e}")

        # Receivers
        try:
            receivers_data = self._get("/api/v2/receivers")
            integrations_found: set[str] = set()
            for r in receivers_data or []:
                name = r.get("name", "unknown")
                receiver_types: list[str] = []
                # AlertManager API v2 doesn't expose receiver types directly,
                # but we can infer from the name and available config keys
                for config_key, integration_name in _KNOWN_INTEGRATIONS.items():
                    if config_key in r or integration_name in name.lower():
                        receiver_types.append(integration_name)
                        integrations_found.add(integration_name)
                result["receivers"].append(
                    AlertReceiver(name=name, receiver_types=receiver_types)
                )
            result["integrations"] = sorted(integrations_found)
            logger.info("  Found %d AlertManager receivers", len(result["receivers"]))
        except AdapterError as e:
            result["errors"].append(f"receivers: {e}")

        # Silences
        try:
            silences = self._get("/api/v2/silences")
            active_silences = [
                s for s in (silences or [])
                if s.get("status", {}).get("state") == "active"
            ]
            result["silences"] = active_silences
            logger.info("  Found %d active silences", len(active_silences))
        except AdapterError as e:
            result["errors"].append(f"silences: {e}")

        # Active alerts count
        try:
            alerts = self._get("/api/v2/alerts")
            result["active_alerts_count"] = len(alerts or [])
            logger.info("  %d active alerts firing", result["active_alerts_count"])
        except AdapterError as e:
            result["errors"].append(f"alerts: {e}")

        return result
