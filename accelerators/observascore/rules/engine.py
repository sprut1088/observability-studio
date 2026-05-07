"""Rules engine.

Loads YAML rule packs and evaluates them against the ObservabilityEstate.
Rules are Python check functions registered by ID and referenced from YAML
metadata (description, severity, weight, dimension, remediation).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

import yaml

from observascore.model import ObservabilityEstate, Severity

logger = logging.getLogger(__name__)

# Registry of rule check functions
CHECK_REGISTRY: dict[str, Callable[[ObservabilityEstate], list[dict]]] = {}


def register(rule_id: str):
    """Decorator to register a rule check function."""
    def decorator(fn: Callable[[ObservabilityEstate], list[dict]]):
        CHECK_REGISTRY[rule_id] = fn
        return fn
    return decorator


@dataclass
class Finding:
    rule_id: str
    dimension: str
    severity: str
    title: str
    description: str
    remediation: str
    weight: int
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "dimension": self.dimension,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "remediation": self.remediation,
            "weight": self.weight,
            "evidence": self.evidence,
        }


@dataclass
class RuleDefinition:
    id: str
    dimension: str
    severity: str
    title: str
    description: str
    remediation: str
    weight: int = 1
    enabled: bool = True


class RulesEngine:
    """Loads rule YAML packs and runs registered checks."""

    def __init__(self, pack_dir: Path | None = None):
        if pack_dir is None:
            pack_dir = Path(__file__).parent / "packs"
        self.pack_dir = pack_dir
        self.rules: dict[str, RuleDefinition] = {}
        self._load_packs()
        # Import check modules to populate registry
        from observascore.rules import checks  # noqa: F401
        from observascore.rules import trend_checks  # noqa: F401
        from observascore.rules import appdynamics_checks  # noqa: F401
        from observascore.rules import datadog_checks  # noqa: F401
        from observascore.rules import dynatrace_checks  # noqa: F401
        from observascore.rules import splunk_checks  # noqa: F401

    def _load_packs(self) -> None:
        """Load all YAML rule packs from pack_dir."""
        if not self.pack_dir.exists():
            logger.warning("Rule pack directory not found: %s", self.pack_dir)
            return
        for yaml_file in sorted(self.pack_dir.glob("*.yaml")):
            logger.info("Loading rule pack: %s", yaml_file.name)
            with open(yaml_file) as f:
                pack = yaml.safe_load(f) or {}
            for rule in pack.get("rules", []):
                rd = RuleDefinition(
                    id=rule["id"],
                    dimension=rule["dimension"],
                    severity=rule["severity"],
                    title=rule["title"],
                    description=rule["description"],
                    remediation=rule.get("remediation", ""),
                    weight=rule.get("weight", 1),
                    enabled=rule.get("enabled", True),
                )
                self.rules[rd.id] = rd
        logger.info("Loaded %d rules", len(self.rules))

    def evaluate(self, estate: ObservabilityEstate) -> list[Finding]:
        """Run all enabled rules against the estate."""
        findings: list[Finding] = []
        for rule_id, rd in self.rules.items():
            if not rd.enabled:
                continue
            check_fn = CHECK_REGISTRY.get(rule_id)
            if not check_fn:
                logger.debug("No check function registered for rule %s", rule_id)
                continue
            try:
                violations = check_fn(estate)
                for v in violations:
                    findings.append(
                        Finding(
                            rule_id=rule_id,
                            dimension=rd.dimension,
                            severity=rd.severity,
                            title=rd.title,
                            description=v.get("description", rd.description),
                            remediation=rd.remediation,
                            weight=rd.weight,
                            evidence=v.get("evidence", []),
                        )
                    )
            except Exception as e:
                logger.error("Rule %s raised an exception: %s", rule_id, e)
        logger.info("Evaluation complete: %d findings", len(findings))
        return findings
