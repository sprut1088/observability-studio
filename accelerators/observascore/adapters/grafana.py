"""Grafana adapter - read-only extraction via HTTP API."""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import Dashboard, DashboardPanel, Datasource, AlertRule, AlertClassification

logger = logging.getLogger(__name__)


class GrafanaAdapter(BaseAdapter):
    tool_name = "grafana"

    def _configure_auth(self) -> None:
        super()._configure_auth()
        api_key = self.config.get("api_key")
        if api_key:
            self.session.headers.update({"Authorization": f"Bearer {api_key}"})
        self.session.headers.update({"Accept": "application/json"})

    def health_check(self) -> bool:
        try:
            data = self._get("/api/health")
            return data.get("database") == "ok"
        except Exception as e:
            logger.error("Grafana health check failed: %s", e)
            return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Grafana at %s", self.url)
        result: dict[str, Any] = {
            "dashboards": [],
            "datasources": [],
            "alert_rules": [],
            "folders": [],
            "errors": [],
        }

        # Folders
        try:
            folders = self._get("/api/folders")
            result["folders"] = folders
            logger.info("  Extracted %d folders", len(folders))
        except AdapterError as e:
            result["errors"].append(f"folders: {e}")

        # Datasources
        try:
            ds_list = self._get("/api/datasources")
            for ds in ds_list:
                result["datasources"].append(
                    Datasource(
                        source_tool=self.tool_name,
                        name=ds.get("name", ""),
                        ds_type=ds.get("type", ""),
                        url=ds.get("url"),
                        is_default=ds.get("isDefault", False),
                    )
                )
            logger.info("  Extracted %d datasources", len(result["datasources"]))
        except AdapterError as e:
            result["errors"].append(f"datasources: {e}")

        # Dashboards - search then fetch each
        try:
            search = self._get("/api/search", params={"type": "dash-db", "limit": 500})
            logger.info("  Found %d dashboards, fetching details...", len(search))
            for item in search:
                uid = item.get("uid")
                if not uid:
                    continue
                try:
                    detail = self._get(f"/api/dashboards/uid/{uid}")
                    dash_json = detail.get("dashboard", {})
                    meta = detail.get("meta", {})
                    panels_raw = self._flatten_panels(dash_json.get("panels", []))
                    panels: list[DashboardPanel] = []
                    for p in panels_raw:
                        queries = []
                        for tgt in p.get("targets", []) or []:
                            q = tgt.get("expr") or tgt.get("query") or tgt.get("rawSql") or ""
                            if q:
                                queries.append(q)
                        field_config = p.get("fieldConfig", {}) or {}
                        defaults = field_config.get("defaults", {}) or {}
                        thresholds = defaults.get("thresholds", {}) or {}
                        has_thresholds = bool(thresholds.get("steps"))
                        unit = defaults.get("unit")
                        panels.append(
                            DashboardPanel(
                                title=p.get("title", ""),
                                panel_type=p.get("type", ""),
                                queries=queries,
                                unit=unit,
                                has_thresholds=has_thresholds,
                                has_legend=True,
                            )
                        )
                    variables = [
                        v.get("name", "")
                        for v in (dash_json.get("templating", {}) or {}).get("list", [])
                    ]
                    result["dashboards"].append(
                        Dashboard(
                            source_tool=self.tool_name,
                            uid=uid,
                            title=dash_json.get("title", "Untitled"),
                            folder=item.get("folderTitle"),
                            tags=dash_json.get("tags", []) or [],
                            panels=panels,
                            variables=variables,
                            has_templating=len(variables) > 0,
                            last_modified=meta.get("updated"),
                            owner=meta.get("updatedBy"),
                            raw={},  # don't keep full raw to save memory
                        )
                    )
                except AdapterError as e:
                    result["errors"].append(f"dashboard {uid}: {e}")
        except AdapterError as e:
            result["errors"].append(f"dashboards: {e}")

        # Alert rules (Grafana unified alerting)
        try:
            rules = self._get("/api/v1/provisioning/alert-rules")
            for r in rules or []:
                name = r.get("title", "")
                # Build a pseudo-expression from the first data query for heuristics
                data_queries = r.get("data", [])
                expr = ""
                if data_queries:
                    model = data_queries[0].get("model", {})
                    expr = model.get("expr") or model.get("query") or ""
                labels = r.get("labels", {}) or {}
                annotations = r.get("annotations", {}) or {}
                runbook = annotations.get("runbook_url") or annotations.get("runbook")
                result["alert_rules"].append(
                    AlertRule(
                        source_tool=self.tool_name,
                        name=name,
                        expression=expr,
                        severity=labels.get("severity"),
                        classification=AlertClassification.UNKNOWN,
                        for_duration=r.get("for"),
                        labels=labels,
                        annotations=annotations,
                        runbook_url=runbook,
                        group=r.get("ruleGroup"),
                        raw={},
                    )
                )
            logger.info("  Extracted %d Grafana alert rules", len(result["alert_rules"]))
        except AdapterError as e:
            # Grafana provisioning API requires admin or specific permissions;
            # this is non-fatal for Viewer tokens
            logger.info("  Grafana alert rules not accessible (likely viewer-only token)")

        return result

    def _flatten_panels(self, panels: list[dict]) -> list[dict]:
        """Flatten rows and nested panels."""
        flat: list[dict] = []
        for p in panels:
            if p.get("type") == "row":
                flat.extend(self._flatten_panels(p.get("panels", []) or []))
            else:
                flat.append(p)
        return flat
