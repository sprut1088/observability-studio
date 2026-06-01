"""Intent classification — thin wrapper around the deterministic
keyword classifier already in `accelerators.ayosa.service`.

Exposed as a standalone module so the agent backbone can use it without
instantiating `AyosaService`, and so unit tests can target it directly.
"""

from __future__ import annotations

from accelerators.ayosa.service import _classify_intent_standalone


def classify_intent(message: str) -> str:
    """Return the canonical intent name for the given user message.

    Pure function. Identical behaviour to the existing service.
    """
    return _classify_intent_standalone(message or "")


__all__ = ["classify_intent"]
