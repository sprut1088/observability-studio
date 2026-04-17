from fastapi import APIRouter
from backend.app.models.connection import ConnectionSchema, ConnectionResponse
from backend.app.services.crawler_service import validate_connection

router = APIRouter()


@router.post(
    "/validate",
    response_model=ConnectionResponse,
    summary="Probe a tool's health endpoint",
    description=(
        "Sends a lightweight HTTP probe to the tool's declared health endpoint "
        "using the connection parameters provided. Returns reachability status "
        "and round-trip latency."
    ),
)
async def validate_tool_connection(conn: ConnectionSchema) -> ConnectionResponse:
    return await validate_connection(conn)
