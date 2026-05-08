from __future__ import annotations
from html import unescape
import logging
from typing import Any

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote
from xml.sax.saxutils import unescape
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
    
    def _extract_studio_queries(self, viz: dict, all_data_sources: dict) -> list[str]:
        queries: list[str] = []

        viz_data_sources = viz.get("dataSources") or {}

        for _, ds_ref in viz_data_sources.items():
            if isinstance(ds_ref, str):
                ds = all_data_sources.get(ds_ref, {})
            elif isinstance(ds_ref, dict):
                ds_id = ds_ref.get("id") or ds_ref.get("primary") or ds_ref.get("dataSource")
                ds = all_data_sources.get(ds_id, ds_ref)
            else:
                ds = {}

            if not isinstance(ds, dict):
                continue

            options = ds.get("options") or {}
            query = options.get("query") or options.get("search") or ds.get("query") or ds.get("search")

            if query:
                queries.append(query)

        return queries

    def _extract_json_from_raw_view(self, raw_view: str) -> str:
        if not raw_view:
            return ""

        text = unescape(raw_view.strip())

        if text.startswith("{") and "visualizations" in text:
            return text

        try:
            root = ET.fromstring(text)
            for node in root.iter():
                node_text = (node.text or "").strip()
                if not node_text:
                    continue

                cleaned = unescape(node_text).strip()
                if cleaned.startswith("{") and "visualizations" in cleaned:
                    return cleaned
        except Exception:
            pass

        match = re.search(r"(\{[\s\S]*\"visualizations\"[\s\S]*\"dataSources\"[\s\S]*\})", text)
        if match:
            return unescape(match.group(1)).strip()

        return ""
    
    def _parse_splunk_dashboard_panels(self, raw_view: str) -> list[DashboardPanel]:
        panels: list[DashboardPanel] = []

        if not raw_view:
            return panels

        studio_json = self._extract_json_from_raw_view(raw_view)

        if studio_json:
            try:
                parsed = json.loads(studio_json)

                visualizations = parsed.get("visualizations") or {}
                data_sources = parsed.get("dataSources") or {}

                for viz_id, viz in visualizations.items():
                    if not isinstance(viz, dict):
                        continue

                    options = viz.get("options") or {}

                    title = (
                        viz.get("title")
                        or options.get("title")
                        or options.get("displayName")
                        or viz_id
                    )

                    panel_type = viz.get("type") or "splunk.studio"

                    panels.append(
                        DashboardPanel(
                            title=title,
                            panel_type=panel_type,
                            queries=self._extract_studio_queries(viz, data_sources),
                            has_thresholds=bool(options.get("thresholds")),
                            has_legend=True,
                        )
                    )

                if panels:
                    return panels

            except Exception:
                pass

        # Classic Simple XML fallback
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

                if not name:
                    continue

                raw_view = content.get("eai:data") or content.get("eai:data_template") or ""

                # Dashboard Studio detail is often only reliable when fetched per dashboard.
                try:
                    detail = self._get(
                        f"/servicesNS/-/{self.app}/data/ui/views/{quote(name, safe='')}",
                        params={"output_mode": "json"},
                    )

                    detail_entries = detail.get("entry") or []
                    if detail_entries:
                        detail_content = detail_entries[0].get("content", {}) or {}
                        raw_view = (
                            detail_content.get("eai:data")
                            or detail_content.get("eai:data_template")
                            or raw_view
                        )

                        label = (
                            detail_content.get("label")
                            or detail_entries[0].get("name")
                            or label
                        )

                except AdapterError as e:
                    result["errors"].append(f"dashboard detail {name}: {e}")

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
                        has_templating="$" in raw_view,
                        last_modified=entry.get("updated"),
                        owner=entry.get("author"),
                        raw={
                            "is_visible": content.get("isVisible"),
                            "description": content.get("description"),
                            "panel_count": len(panels),
                            "dashboard_source_type": "json" if raw_view.strip().startswith("{") else "xml",
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