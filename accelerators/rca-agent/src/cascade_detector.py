"""
cascade_detector.py
────────────────────
Traces how a primary anomaly propagates through the service dependency graph.
Reads service topology from correlation_rules.yaml if available; otherwise
infers topology from collected Jaeger services.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


class CascadeDetector:
    """
    Traces error cascades through a service dependency graph.

    The graph is built from two sources (merged):
      1. Static mapping in correlation_rules.yaml (service_dependencies section)
      2. Dynamic inference: services observed in Jaeger traces are assumed
         to form a flat peer graph if no static config is provided.
    """

    def __init__(self, service_graph: dict[str, list[str]] | None = None):
        static_graph = self._load_static_graph()
        self.service_graph: dict[str, list[str]] = {**static_graph, **(service_graph or {})}

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def detect_cascade(
        self,
        root_services: list[str],
        observed_services: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Given the primary root-cause service(s), find all impacted downstream
        services and build a cascade chain.

        Args:
            root_services:      Services identified as root causes.
            observed_services:  All services seen in traces (used when no
                                static graph is configured).

        Returns:
            {
                direct_dependents:    [...],
                indirect_dependents:  [...],
                cascade_chain:        ["svcA → svcB → svcC"],
                blast_radius:         int,
                all_affected:         [...],
            }
        """
        if observed_services:
            self._infer_graph_from_observations(root_services, observed_services)

        direct:   set[str] = set()
        indirect: set[str] = set()

        for svc in root_services:
            direct   |= set(self._find_direct_dependents(svc))
            indirect |= set(self._find_indirect_dependents(svc, depth=3))

        indirect -= direct  # keep sets mutually exclusive

        all_affected = sorted(set(root_services) | direct | indirect)
        chain = self._build_cascade_chain(root_services, direct, indirect)

        return {
            "root_services":       root_services,
            "direct_dependents":   sorted(direct),
            "indirect_dependents": sorted(indirect),
            "cascade_chain":       chain,
            "blast_radius":        len(all_affected),
            "all_affected":        all_affected,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _find_direct_dependents(self, service: str) -> list[str]:
        return self.service_graph.get(service, [])

    def _find_indirect_dependents(self, service: str, depth: int = 3) -> list[str]:
        """BFS up to `depth` hops from `service`."""
        found: set[str] = set()
        queue: list[tuple[str, int]] = [(service, 0)]
        visited: set[str] = {service}

        while queue:
            current, level = queue.pop(0)
            if level >= depth:
                continue
            for dep in self._find_direct_dependents(current):
                if dep not in visited:
                    visited.add(dep)
                    found.add(dep)
                    queue.append((dep, level + 1))

        return list(found)

    def _build_cascade_chain(
        self,
        root_services: list[str],
        direct: set[str],
        indirect: set[str],
    ) -> list[str]:
        chain: list[str] = []
        chain.append(" | ".join(root_services) + "  [ROOT CAUSE]")
        if direct:
            chain.append("  → " + " | ".join(sorted(direct)) + "  [directly impacted]")
        if indirect:
            chain.append("    → " + " | ".join(sorted(indirect)) + "  [transitively impacted]")
        return chain

    def _infer_graph_from_observations(
        self,
        root_services: list[str],
        observed_services: list[str],
    ) -> None:
        """
        When no static graph exists, assume root services call all other
        observed services (flat heuristic).
        """
        for svc in root_services:
            if svc not in self.service_graph:
                self.service_graph[svc] = [
                    s for s in observed_services if s != svc
                ]

    def _load_static_graph(self) -> dict[str, list[str]]:
        """Load service_dependencies from correlation_rules.yaml."""
        path = _CONFIG_DIR / "correlation_rules.yaml"
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            deps = data.get("service_dependencies", {})
            # Normalise: each entry has a "downstream" list
            graph: dict[str, list[str]] = {}
            for svc, mapping in deps.items():
                if isinstance(mapping, dict):
                    graph[svc] = mapping.get("downstream", [])
                elif isinstance(mapping, list):
                    graph[svc] = mapping
            return graph
        except Exception as exc:
            logger.warning("Could not load correlation_rules.yaml: %s", exc)
            return {}
