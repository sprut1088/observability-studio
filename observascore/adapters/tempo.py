"""Grafana Tempo adapter - read-only extraction.

Tempo is the OTel-native distributed tracing backend in the Grafana stack.
Its presence signals OpenTelemetry adoption. We extract service names and
tag cardinality to understand tracing coverage.
"""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import Service, Signal, SignalType

logger = logging.getLogger(__name__)


class TempoAdapter(BaseAdapter):
    tool_name = "tempo"

    def health_check(self) -> bool:
        try:
            resp = self.session.get(
                f"{self.url}/ready", timeout=self.timeout, verify=self.verify_tls
            )
            return resp.status_code == 200
        except Exception:
            try:
                self._get("/api/echo")
                return True
            except Exception as e:
                logger.error("Tempo health check failed: %s", e)
                return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Tempo at %s", self.url)
        result: dict[str, Any] = {
            "services": [],
            "tag_keys": [],
            "signals": [],
            "otel_native": True,  # Tempo always = OTel native tracing
            "errors": [],
        }

        # Tag keys - tells us what attributes are being propagated
        try:
            tags_resp = self._get("/api/v2/search/tags")
            tag_scopes = tags_resp.get("scopes", []) or []
            all_tags: list[str] = []
            for scope in tag_scopes:
                all_tags.extend(scope.get("tags", []) or [])
            # Fallback to v1 API
            if not all_tags:
                v1_resp = self._get("/api/search/tags")
                all_tags = v1_resp.get("tagNames", []) or []
            result["tag_keys"] = all_tags
            logger.info("  Found %d Tempo tag keys", len(all_tags))
        except AdapterError as e:
            result["errors"].append(f"tags: {e}")

        # Extract service names from service.name tag values
        try:
            svc_resp = self._get("/api/v2/search/tag/service.name/values")
            service_names = svc_resp.get("tagValues", []) or []
            # v1 fallback
            if not service_names:
                svc_v1 = self._get("/api/search/tag/service.name/values")
                service_names = [v.get("value", v) if isinstance(v, dict) else v
                                 for v in (svc_v1.get("tagValues", []) or [])]
            for svc_name in service_names:
                name = svc_name.get("value", svc_name) if isinstance(svc_name, dict) else svc_name
                result["services"].append(
                    Service(name=str(name), source_tool=self.tool_name)
                )
                result["signals"].append(
                    Signal(
                        source_tool=self.tool_name,
                        identifier=str(name),
                        signal_type=SignalType.TRACE,
                    )
                )
            logger.info("  Found %d traced services in Tempo", len(result["services"]))
        except AdapterError as e:
            # Not all Tempo versions expose tag value search; non-fatal
            result["errors"].append(f"service names: {e}")

        # Sample recent traces to check OTel attribute richness
        try:
            search_resp = self._get("/api/search", params={"limit": 20})
            traces = search_resp.get("traces", []) or []
            # Detect OTel resource attributes presence
            otel_attrs = {"service.name", "deployment.environment", "service.version"}
            seen_root_names: set[str] = set()
            for trace in traces:
                root_service = trace.get("rootServiceName", "")
                if root_service:
                    seen_root_names.add(root_service)
            # Add any services not already found via tag search
            for svc_name in seen_root_names:
                if not any(s.name == svc_name for s in result["services"]):
                    result["services"].append(
                        Service(name=svc_name, source_tool=self.tool_name)
                    )
                    result["signals"].append(
                        Signal(
                            source_tool=self.tool_name,
                            identifier=svc_name,
                            signal_type=SignalType.TRACE,
                        )
                    )
            logger.info("  Sampled %d recent traces", len(traces))
        except AdapterError as e:
            result["errors"].append(f"trace search: {e}")

        return result
