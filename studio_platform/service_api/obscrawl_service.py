"""
studio_platform.service_api.obscrawl_service
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service boundary for the ObsCrawl accelerator.

Placeholder for MCP / SaaS exposure.  Crawl logic lives in
accelerators/obscrawl/ and backend/app/services/crawler_service.py.
"""


class ObsCrawlService:
    """Platform-level service facade for ObsCrawl."""

    def run_crawler(self) -> dict:
        """Trigger a full tool extraction and return an Excel export URL.

        Not yet implemented — will delegate to accelerators/obscrawl/service.py.
        """
        pass
