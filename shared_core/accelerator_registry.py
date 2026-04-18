"""
shared_core.accelerator_registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Base class for all Observability Studio accelerators.

Each accelerator (ObservaScore, ObsCrawl, …) subclasses Accelerator and is
registered here to support plug-and-play discovery by the platform layer.

Usage:
    from shared_core.accelerator_registry import Accelerator

    class MyAccelerator(Accelerator):
        def run(self, context):
            ...
"""

from __future__ import annotations


class Accelerator:
    """Abstract base class for a platform accelerator module."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, context: dict) -> dict:
        """Execute the accelerator with the supplied context dict.

        Subclasses must override this method.
        """
        raise NotImplementedError(f"Accelerator '{self.name}' has not implemented run()")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Accelerator name={self.name!r}>"
