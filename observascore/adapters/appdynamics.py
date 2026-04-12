"""AppDynamics adapter - read-only extraction via the AppDynamics REST API.

Extracts per-application topology (tiers, nodes), health rules, business
transactions, baselines, dashboards, EUM apps, and infrastructure monitoring
state. All data is normalised into the Common Observability Model (COM).

Auth: AppDynamics uses Basic auth with the form  ``username@account:password``
plus an optional OAuth2 client-credentials flow. We support both; if
``client_id`` / ``client_secret`` are present we get a Bearer token first.
"""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    DashboardPanel,
    Service,
    Signal,
    SignalType,
)

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "WARNING": "warning",
    "INFO": "info",
}


class AppDynamicsAdapter(BaseAdapter):
    """Read-only adapter for the AppDynamics Controller REST API."""

    tool_name = "appdynamics"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.account = config.get("account", "")
        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")
        self._bearer_token: str | None = None
        # AppDynamics requires JSON output via query param on most v1 endpoints
        self._json_params: dict = {"output": "JSON"}

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _configure_auth(self) -> None:
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        account = self.config.get("account", "")
        if username and password:
            # AppDynamics format: username@account
            full_user = f"{username}@{account}" if account and "@" not in username else username
            self.session.auth = (full_user, password)
        self.session.headers.update({"Accept": "application/json"})

    def _get_oauth_token(self) -> str | None:
        """Fetch OAuth2 Bearer token using client credentials."""
        if not (self.client_id and self.client_secret and self.account):
            return None
        try:
            resp = self.session.post(
                f"{self.url}/controller/api/oauth/access_token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": f"{self.client_id}@{self.account}",
                    "client_secret": self.client_secret,
                },
                timeout=self.timeout,
                verify=self.verify_tls,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
            if token:
                self.session.headers.update({"Authorization": f"Bearer {token}"})
                self.session.auth = None  # Bearer overrides basic auth
            return token
        except Exception as e:
            logger.warning("AppDynamics OAuth token fetch failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        if self.client_id and self.client_secret:
            self._bearer_token = self._get_oauth_token()
        try:
            data = self._get("/controller/rest/applications", params=self._json_params)
            return isinstance(data, list)
        except Exception as e:
            logger.error("AppDynamics health check failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Extract
    # ------------------------------------------------------------------

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from AppDynamics at %s", self.url)
        result: dict[str, Any] = {
            "applications": [],
            "services": [],
            "alert_rules": [],
            "dashboards": [],
            "signals": [],
            "business_transactions_total": 0,
            "has_eum": False,
            "has_sim": False,
            "has_db_monitoring": False,
            "apps_with_baselines": 0,
            "errors": [],
        }

        # Applications
        apps = self._fetch_applications(result)

        # Per-application deep data (capped to 20 apps for large environments)
        for app in apps[:20]:
            app_id = app.get("id")
            app_name = app.get("name", str(app_id))
            self._fetch_tiers(app_id, app_name, result)
            self._fetch_health_rules(app_id, app_name, result)
            self._fetch_business_transactions(app_id, app_name, result)
            self._check_baselines(app_id, app_name, result)

        # Dashboards
        self._fetch_dashboards(result)

        # EUM (browser/mobile monitoring)
        self._fetch_eum(result)

        # Server Infrastructure Monitoring (SIM)
        self._fetch_sim_status(result)

        # Database monitoring
        self._fetch_db_monitoring(result)

        logger.info(
            "AppDynamics: %d apps, %d tiers, %d health rules, %d BTs, %d dashboards",
            len(result["applications"]),
            len(result["services"]),
            len(result["alert_rules"]),
            result["business_transactions_total"],
            len(result["dashboards"]),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_applications(self, result: dict) -> list[dict]:
        try:
            apps = self._get("/controller/rest/applications", params=self._json_params)
            result["applications"] = apps or []
            for app in apps or []:
                result["signals"].append(Signal(
                    source_tool=self.tool_name,
                    identifier=app.get("name", ""),
                    signal_type=SignalType.TRACE,
                    semantic_type="traffic",
                ))
            return apps or []
        except AdapterError as e:
            result["errors"].append(f"applications: {e}")
            return []

    def _fetch_tiers(self, app_id: int, app_name: str, result: dict) -> None:
        try:
            tiers = self._get(
                f"/controller/rest/applications/{app_id}/tiers",
                params=self._json_params,
            )
            for tier in tiers or []:
                result["services"].append(Service(
                    name=f"{app_name}/{tier.get('name', '')}",
                    source_tool=self.tool_name,
                    tier=app_name,
                ))
                # Tiers with agents emit APM signals
                if tier.get("numberOfNodes", 0) > 0:
                    result["signals"].append(Signal(
                        source_tool=self.tool_name,
                        identifier=tier.get("name", ""),
                        signal_type=SignalType.METRIC,
                        semantic_type="latency",
                        labels={"app": app_name},
                    ))
        except AdapterError as e:
            result["errors"].append(f"tiers/{app_name}: {e}")

    def _fetch_health_rules(self, app_id: int, app_name: str, result: dict) -> None:
        try:
            rules = self._get(
                f"/controller/rest/applications/{app_id}/health-rules",
                params=self._json_params,
            )
            for rule in rules or []:
                name = rule.get("name", "")
                enabled = rule.get("enabled", True)
                if not enabled:
                    continue
                # AppDynamics health rules fire on degraded/critical conditions
                raw_sev = rule.get("criticalCriteria", {}).get("type", "WARNING")
                severity = "critical" if "CRITICAL" in str(raw_sev).upper() else "warning"
                result["alert_rules"].append(AlertRule(
                    source_tool=self.tool_name,
                    name=f"{app_name}: {name}",
                    expression=f"appdynamics:health_rule:{app_id}:{name}",
                    severity=severity,
                    classification=AlertClassification.SYMPTOM,
                    labels={"app": app_name},
                    annotations={
                        "description": rule.get("description", ""),
                        "summary": name,
                    },
                    # AppDynamics notifies via Actions — no inline runbook URL in rule API
                    runbook_url=None,
                    group=app_name,
                    raw=rule,
                ))
        except AdapterError as e:
            result["errors"].append(f"health_rules/{app_name}: {e}")

    def _fetch_business_transactions(self, app_id: int, app_name: str, result: dict) -> None:
        try:
            bts = self._get(
                f"/controller/rest/applications/{app_id}/business-transactions",
                params=self._json_params,
            )
            active_bts = [bt for bt in (bts or []) if not bt.get("internalName", "").startswith("OVERFLOW")]
            result["business_transactions_total"] += len(active_bts)
            # Each active BT represents a traced endpoint — adds latency + traffic signals
            for bt in active_bts[:50]:
                result["signals"].append(Signal(
                    source_tool=self.tool_name,
                    identifier=bt.get("name", ""),
                    signal_type=SignalType.TRACE,
                    semantic_type="latency",
                    labels={"app": app_name, "tier": bt.get("tierName", "")},
                ))
        except AdapterError as e:
            result["errors"].append(f"business_transactions/{app_name}: {e}")

    def _check_baselines(self, app_id: int, app_name: str, result: dict) -> None:
        """Check whether dynamic baselines are configured for this application."""
        try:
            # AppDynamics baselines endpoint
            baselines = self._get(
                f"/controller/rest/applications/{app_id}/baselines",
                params=self._json_params,
            )
            if baselines:
                result["apps_with_baselines"] = result.get("apps_with_baselines", 0) + 1
        except AdapterError:
            # Baseline API may not be available in all controller versions — non-fatal
            pass

    def _fetch_dashboards(self, result: dict) -> None:
        try:
            dashboards = self._get("/controller/rest/dashboards", params=self._json_params)
            for d in dashboards or []:
                result["dashboards"].append(Dashboard(
                    source_tool=self.tool_name,
                    uid=str(d.get("id", "")),
                    title=d.get("name", "Untitled"),
                    folder=None,
                    tags=[],
                ))
        except AdapterError as e:
            result["errors"].append(f"dashboards: {e}")

    def _fetch_eum(self, result: dict) -> None:
        """Check for End User Monitoring (browser/mobile apps)."""
        try:
            eum_apps = self._get("/controller/rest/eum/apps/list", params=self._json_params)
            result["has_eum"] = bool(eum_apps)
            if eum_apps:
                logger.info("  AppDynamics EUM: %d browser/mobile apps", len(eum_apps))
        except AdapterError:
            # EUM endpoint may require separate license — non-fatal
            pass

    def _fetch_sim_status(self, result: dict) -> None:
        """Check if Server Infrastructure Monitoring is enabled."""
        try:
            sim_config = self._get(
                "/controller/rest/configuration",
                params={"name": "sim.enabled", "output": "JSON"},
            )
            if isinstance(sim_config, list):
                for item in sim_config:
                    if item.get("name") == "sim.enabled" and item.get("value") == "true":
                        result["has_sim"] = True
        except AdapterError:
            pass

    def _fetch_db_monitoring(self, result: dict) -> None:
        """Check if Database Monitoring is active."""
        try:
            db_servers = self._get(
                "/controller/rest/databases/servers",
                params=self._json_params,
            )
            result["has_db_monitoring"] = bool(db_servers)
        except AdapterError:
            pass
