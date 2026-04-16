from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

BASE_RUNTIME_DIR = Path("runtime").resolve()

@router.get("/download/{file_path:path}")
def download_file(file_path: str):
    full_path = Path(file_path).resolve()

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Safety check: only allow downloads from runtime folder
    if BASE_RUNTIME_DIR not in full_path.parents and full_path != BASE_RUNTIME_DIR:
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )
