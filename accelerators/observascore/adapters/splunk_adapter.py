from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    Signal,
    SignalType,
)

logger = logging.getLogger(__name__)


class SplunkAdapter(BaseAdapter):
    tool_name = "splunk"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

        self.base_url = (
            config.get("splunk_base_url")
            or config.get("url")
            or ""
        ).rstrip("/")

        self.mgmt_url = (
            config.get("splunk_mgmt_url")
            or self.base_url.replace(":8000", ":8089")
        ).rstrip("/")

        self.hec_url = (config.get("splunk_hec_url") or "").rstrip("/")
        self.hec_token = config.get("splunk_hec_token")
        self.app = config.get("splunk_app") or "search"

        # BaseAdapter uses self.url for _get()
        self.url = self.mgmt_url

    def health_check(self) -> bool:
        try:
            data = self._get("/services/server/info", params={"output_mode": "json"})
            return "entry" in data
        except Exception as e:
            logger.error("Splunk health check failed: %s", e)
            return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Splunk at %s", self.mgmt_url)

        result: dict[str, Any] = {
            "dashboards": [],
            "alert_rules": [],
            "signals": [],
            "indexes_count": 0,
            "saved_searches_count": 0,
            "hec_configured": bool(self.hec_url and self.hec_token),
            "errors": [],
        }

        # Dashboards
        try:
            data = self._get(
                f"/servicesNS/-/{self.app}/data/ui/views",
                params={"output_mode": "json", "count": 1000},
            )

            for entry in data.get("entry", []):
                content = entry.get("content", {}) or {}
                name = entry.get("name") or ""
                label = content.get("label") or name

                result["dashboards"].append(
                    Dashboard(
                        source_tool=self.tool_name,
                        uid=name,
                        title=label,
                        folder=self.app,
                        tags=["splunk"],
                        panels=[],
                        variables=[],
                        has_templating=False,
                        last_modified=entry.get("updated"),
                        owner=entry.get("author"),
                        raw={
                            "is_visible": content.get("isVisible"),
                            "description": content.get("description"),
                        },
                    )
                )

            logger.info(" Extracted %d Splunk dashboards", len(result["dashboards"]))

        except AdapterError as e:
            result["errors"].append(f"dashboards: {e}")

        # Saved searches / alerts
        try:
            data = self._get(
                "/servicesNS/-/-/saved/searches",
                params={"output_mode": "json", "count": 1000},
            )

            entries = data.get("entry", [])
            result["saved_searches_count"] = len(entries)

            for entry in entries:
                content = entry.get("content", {}) or {}

                is_scheduled = str(content.get("is_scheduled", "0")).lower() in (
                    "1",
                    "true",
                )

                has_alert_action = bool(content.get("actions")) or bool(
                    content.get("alert_type")
                )

                if not is_scheduled and not has_alert_action:
                    continue

                result["alert_rules"].append(
                    AlertRule(
                        source_tool=self.tool_name,
                        name=entry.get("name") or "",
                        expression=content.get("search") or "",
                        severity=content.get("alert.severity"),
                        classification=AlertClassification.UNKNOWN,
                        for_duration=None,
                        labels={
                            "app": self.app,
                            "splunk_actions": str(content.get("actions") or ""),
                        },
                        annotations={
                            "cron_schedule": str(content.get("cron_schedule") or ""),
                            "alert_type": str(content.get("alert_type") or ""),
                        },
                        runbook_url=None,
                        group="splunk_saved_searches",
                        raw={},
                    )
                )

            logger.info(" Extracted %d Splunk alerts", len(result["alert_rules"]))

        except AdapterError as e:
            result["errors"].append(f"saved_searches: {e}")

        # Indexes as log signals
        try:
            data = self._get(
                "/services/data/indexes",
                params={"output_mode": "json", "count": 1000},
            )

            entries = data.get("entry", [])
            result["indexes_count"] = len(entries)

            for entry in entries:
                name = entry.get("name") or ""
                if not name:
                    continue

                result["signals"].append(
                    Signal(
                        source_tool=self.tool_name,
                        identifier=f"splunk_index:{name}",
                        signal_type=SignalType.LOG,
                        semantic_type="log_index",
                        labels={
                            "index": name,
                            "tool": "splunk",
                        },
                    )
                )

            logger.info(" Extracted %d Splunk indexes", result["indexes_count"])

        except AdapterError as e:
            result["errors"].append(f"indexes: {e}")

        return result