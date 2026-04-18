"""
ObsCrawl service layer.

This module re-exports the crawler service so platform code can import from
a stable accelerator-level path rather than reaching into backend.app.services.

All crawl logic lives in backend/app/services/crawler_service.py and is
preserved without modification to ensure backward compatibility.
"""

from backend.app.services.crawler_service import (
    validate_connection,
    run_crawl,
    TOOLS,
)

__all__ = ["validate_connection", "run_crawl", "TOOLS"]
