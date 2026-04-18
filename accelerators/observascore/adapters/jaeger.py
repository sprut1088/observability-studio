"""Jaeger adapter - read-only extraction."""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import Service, Signal, SignalType

logger = logging.getLogger(__name__)


class JaegerAdapter(BaseAdapter):
    tool_name = "jaeger"

    def health_check(self) -> bool:
        try:
            data = self._get("/api/services")
            return "data" in data
        except Exception as e:
            logger.error("Jaeger health check failed: %s", e)
            return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Jaeger at %s", self.url)
        result: dict[str, Any] = {
            "services": [],
            "signals": [],
            "errors": [],
        }

        try:
            services_resp = self._get("/api/services")
            service_names = services_resp.get("data", []) or []
            logger.info("  Found %d Jaeger services", len(service_names))

            for svc_name in service_names:
                try:
                    ops_resp = self._get(
                        f"/api/services/{svc_name}/operations"
                    )
                    operations = ops_resp.get("data", []) or []
                except AdapterError:
                    operations = []

                result["services"].append(
                    Service(
                        name=svc_name,
                        source_tool=self.tool_name,
                        operations=operations[:100],
                    )
                )
                result["signals"].append(
                    Signal(
                        source_tool=self.tool_name,
                        identifier=svc_name,
                        signal_type=SignalType.TRACE,
                        cardinality_estimate=len(operations),
                    )
                )
        except AdapterError as e:
            result["errors"].append(f"services: {e}")

        return result
