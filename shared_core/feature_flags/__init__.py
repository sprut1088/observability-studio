"""
shared_core.feature_flags
~~~~~~~~~~~~~~~~~~~~~~~~~
Feature flag loader utility.

Usage:
    from shared_core.feature_flags import load_feature_flags

    flags = load_feature_flags()
    if flags.get("observascore"):
        # ObservaScore APIs are active
"""

import yaml
from pathlib import Path

_DEFAULT_FLAGS_PATH = Path(__file__).parent.parent.parent / "platform" / "config" / "feature_flags.yaml"


def load_feature_flags(path: Path | None = None) -> dict:
    """Load feature flags from YAML.  Returns an empty dict on any read error."""
    flags_path = Path(path) if path else _DEFAULT_FLAGS_PATH
    try:
        with open(flags_path, "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
