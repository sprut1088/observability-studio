"""Loki adapter - read-only extraction."""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import Signal, SignalType

logger = logging.getLogger(__name__)


class LokiAdapter(BaseAdapter):
    tool_name = "loki"

    def health_check(self) -> bool:
        try:
            # Loki exposes /ready
            resp = self.session.get(
                f"{self.url}/ready", timeout=self.timeout, verify=self.verify_tls
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error("Loki health check failed: %s", e)
            return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Loki at %s", self.url)
        result: dict[str, Any] = {
            "labels": [],
            "label_values": {},
            "signals": [],
            "errors": [],
        }

        # Labels
        try:
            labels_resp = self._get("/loki/api/v1/labels")
            labels = labels_resp.get("data", []) or []
            result["labels"] = labels
            logger.info("  Found %d Loki labels", len(labels))

            # For each label, get distinct values (capped)
            for label in labels[:30]:
                try:
                    values_resp = self._get(f"/loki/api/v1/label/{label}/values")
                    values = values_resp.get("data", []) or []
                    result["label_values"][label] = values
                    result["signals"].append(
                        Signal(
                            source_tool=self.tool_name,
                            identifier=label,
                            signal_type=SignalType.LOG,
                            cardinality_estimate=len(values),
                            semantic_type=None,
                            labels={},
                        )
                    )
                except AdapterError:
                    continue
        except AdapterError as e:
            result["errors"].append(f"labels: {e}")

        return result
