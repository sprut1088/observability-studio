from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

BASE_RUNTIME_DIR = Path("runtime").resolve()


def _resolve_runtime_path(file_path: str) -> Path:
    full_path = (BASE_RUNTIME_DIR / file_path).resolve()

    try:
        full_path.relative_to(BASE_RUNTIME_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    return full_path


@router.get("/preview/runtime/{file_path:path}")
def preview_runtime_html(file_path: str):
    """
    Serve generated HTML reports inline for iframe-based preview.
    """
    full_path = _resolve_runtime_path(file_path)

    if full_path.suffix.lower() != ".html":
        raise HTTPException(status_code=400, detail="Preview is only supported for HTML reports")

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="text/html",
        headers={"Content-Disposition": f'inline; filename="{full_path.name}"'},
    )


@router.get("/download/runtime/{file_path:path}")
def download_runtime_file(file_path: str):
    """
    Serve files from the runtime/ directory.
    file_path is relative to the runtime/ root, e.g.
      abc123/reports/observascore-report.html
    """
    full_path = _resolve_runtime_path(file_path)

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )


@router.get("/download/{file_path:path}")
def download_file(file_path: str):
    """
    Legacy download route — resolves the path relative to CWD.
    Kept for backwards compatibility with older download URLs.
    """
    full_path = Path(file_path).resolve()

    # Safety: only allow downloads from runtime folder
    if BASE_RUNTIME_DIR not in full_path.parents and full_path != BASE_RUNTIME_DIR:
        raise HTTPException(status_code=403, detail="Access denied")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )
