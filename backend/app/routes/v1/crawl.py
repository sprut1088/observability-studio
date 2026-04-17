from fastapi import APIRouter, HTTPException
from backend.app.models.connection import ConnectionSchema
from backend.app.services.crawler_service import run_crawl

router = APIRouter()


@router.post(
    "/crawl",
    summary="Extract tool data and export to Excel",
    description=(
        "Runs the observascore extraction pipeline for a single tool and "
        "produces a multi-sheet Excel workbook. Returns a download_url that "
        "can be used to retrieve the file via GET /api/download/…"
    ),
)
async def crawl_tool(conn: ConnectionSchema) -> dict:
    try:
        return await run_crawl(conn)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
