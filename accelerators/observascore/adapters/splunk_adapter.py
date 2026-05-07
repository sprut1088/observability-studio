from __future__ import annotations

import logging
from typing import Any

import json
import xml.etree.ElementTree as ET
from observascore.model import DashboardPanel

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
        self.verify_tls = config.get("splunk_verify_ssl", config.get("verify_tls", False))

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

    def _parse_splunk_dashboard_panels(self, raw_view: str) -> list[DashboardPanel]:
        panels: list[DashboardPanel] = []

        if not raw_view:
            return panels

        # Dashboard Studio JSON
        try:
            parsed = json.loads(raw_view)

            visualizations = parsed.get("visualizations", {}) or {}
            layout = parsed.get("layout", {}) or {}
            data_sources = parsed.get("dataSources", {}) or {}

            # Map visualization id -> title/type
            for viz_id, viz in visualizations.items():
                options = viz.get("options", {}) or {}

                panel_title = (
                    options.get("title")
                    or options.get("displayName")
                    or viz.get("title")
                    or viz_id
                )

                panel_type = viz.get("type") or "studio"

                queries = []

                # Studio usually links visualizations to data sources by id
                viz_data_sources = viz.get("dataSources", {}) or {}
                for _, ds_ref in viz_data_sources.items():
                    if isinstance(ds_ref, str):
                        ds = data_sources.get(ds_ref, {}) or {}
                    elif isinstance(ds_ref, dict):
                        ds_id = ds_ref.get("primary") or ds_ref.get("id")
                        ds = data_sources.get(ds_id, {}) if ds_id else ds_ref
                    else:
                        ds = {}

                    options = ds.get("options", {}) or {}
                    query = (
                        options.get("query")
                        or options.get("search")
                        or ds.get("query")
                        or ds.get("search")
                        or ""
                    )

                    if query:
                        queries.append(query)

                panels.append(
                    DashboardPanel(
                        title=panel_title,
                        panel_type=panel_type,
                        queries=queries,
                        has_thresholds=False,
                        has_legend=True,
                    )
                )

            # Some Studio dashboards expose layout structure even if visualizations parsing missed
            if not panels:
                for item in layout.get("structure", []):
                    item_id = item.get("item")
                    if not item_id:
                        continue

                    viz = visualizations.get(item_id, {}) or {}
                    options = viz.get("options", {}) or {}

                    panels.append(
                        DashboardPanel(
                            title=options.get("title") or item_id,
                            panel_type=viz.get("type") or "studio",
                            queries=[],
                            has_thresholds=False,
                            has_legend=True,
                        )
                    )

            return panels

        except Exception:
            pass

        # Classic Simple XML
        try:
            root = ET.fromstring(raw_view)

            for panel in root.findall(".//panel"):
                title_node = panel.find("title")
                title = (
                    title_node.text.strip()
                    if title_node is not None and title_node.text
                    else "Untitled Panel"
                )

                panel_type = "panel"
                for child in panel:
                    if child.tag in ("chart", "table", "single", "event", "map", "html"):
                        panel_type = child.tag
                        break

                queries = []
                for query_node in panel.findall(".//query"):
                    if query_node.text:
                        queries.append(query_node.text.strip())

                panels.append(
                    DashboardPanel(
                        title=title,
                        panel_type=panel_type,
                        queries=queries,
                        has_thresholds=False,
                        has_legend=True,
                    )
                )

        except Exception:
            return panels

        return panels

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

                # Important: actual dashboard source is usually in content["eai:data"]
                raw_view = content.get("eai:data") or content.get("eai:data_template") or ""
                
                panels = self._parse_splunk_dashboard_panels(raw_view)

                result["dashboards"].append(
                    Dashboard(
                        source_tool=self.tool_name,
                        uid=name,
                        title=label,
                        folder=self.app,
                        tags=["splunk"],
                        panels=panels,
                        variables=[],
                        has_templating="${" in raw_view or "$" in raw_view,
                        last_modified=entry.get("updated"),
                        owner=entry.get("author"),
                        raw={
                            "is_visible": content.get("isVisible"),
                            "description": content.get("description"),
                            "panel_count": len(panels),
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