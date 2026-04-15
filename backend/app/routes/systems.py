from fastapi import APIRouter
from backend.app.schemas import ToolConfig, ValidationResponse
from backend.app.services.validator import validate_tool

router = APIRouter()

@router.post("/validate", response_model=ValidationResponse)
def validate_system(tool: ToolConfig):
    ok, message = validate_tool(tool.model_dump())
    return ValidationResponse(system=tool.name, reachable=ok, message=message)