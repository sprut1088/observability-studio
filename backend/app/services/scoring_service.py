"""
scoring_service.py
──────────────────
Runs the ObservaScore assessment pipeline for the Hub tile (/api/v1/assess).

Branch logic:
  use_ai=False  →  _run_internal_engine()   (deterministic rules only)
  use_ai=True   →  _run_with_ai()           (rules + LLM gap analysis)

Both paths call the existing `observascore.cli assess` subprocess so the
full adapter → rules → scoring → report pipeline is reused exactly as-is.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import yaml

from backend.app.models.assessment import AssessmentRequest, AssessmentResponse

# ── Paths ─────────────────────────────────────────────────────────────────────
RUNTIME_DIR = Path("runtime")
BASE_URL = "http://10.235.21.132:8000"


def _build_runtime_urls(file_path: Path) -> tuple[str, str]:
    rel = file_path.relative_to(RUNTIME_DIR)
    rel_path = rel.as_posix()
    return (
        f"{BASE_URL}/api/preview/runtime/{rel_path}",
        f"{BASE_URL}/api/download/runtime/{rel_path}",
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def run_scoring(req: AssessmentRequest) -> AssessmentResponse:
    """
    Build a runtime config and dispatch to the appropriate scoring path.

    Returns an AssessmentResponse with a download_url pointing to the
    generated HTML report served via /api/download.
    """
    run_id = uuid.uuid4().hex
    workdir = RUNTIME_DIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)

    config_path = _write_assess_config(req, workdir)
    output_dir = workdir / "reports"
    output_dir.mkdir(exist_ok=True)

    # ── Branch on use_ai ──────────────────────────────────────────────────
    if req.use_ai:
        result = await _run_with_ai(config_path, output_dir)
    else:
        result = await _run_internal_engine(config_path, output_dir)

    # ── Locate generated report ───────────────────────────────────────────
    html_files = sorted(
        output_dir.glob("*.html"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not html_files:
        error_detail = result.get("stderr", "")[:300] or "No HTML report produced"
        return AssessmentResponse(
            success=False,
            message=f"Assessment failed — {error_detail}",
        )

    json_files = sorted(
        output_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    html_path = html_files[0]
    preview_url, download_url = _build_runtime_urls(html_path)
    json_url = None
    if json_files:
        _, json_url = _build_runtime_urls(json_files[0])

    mode = "AI-powered" if req.use_ai else "deterministic"
    return AssessmentResponse(
        success=True,
        message=f"Assessment complete ({mode} scoring)",
        preview_url=preview_url,
        download_url=download_url,
        json_url=json_url,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _write_assess_config(req: AssessmentRequest, workdir: Path) -> Path:
    """
    Map AssessmentRequest → observascore config.yaml structure and persist
    it to the runtime workdir.
    """
    tool_key = req.tool_source.lower()
    source: dict[str, Any] = {"enabled": True, "url": req.api_endpoint}
    if req.auth_token:
        source["api_key"] = req.auth_token

    ai_cfg: dict[str, Any] = {"enabled": req.use_ai}
    if req.use_ai:
        provider = (req.ai_provider or "anthropic").lower()
        ai_cfg["provider"] = provider
        if req.ai_api_key:
            ai_cfg["api_key"] = req.ai_api_key
        if provider in ("azure", "azure_openai", "openai_azure"):
            if req.azure_endpoint:
                ai_cfg["azure_endpoint"] = req.azure_endpoint
            if req.azure_deployment:
                ai_cfg["azure_deployment"] = req.azure_deployment
            if req.azure_api_version:
                ai_cfg["azure_api_version"] = req.azure_api_version

    config = {
        "client": {
            "name": req.tool_source.capitalize(),
            "environment": "hub-assess",
        },
        "sources": {tool_key: source},
        "ai": ai_cfg,
    }

    path = workdir / "config.yaml"
    with open(path, "w") as fh:
        yaml.dump(config, fh, default_flow_style=False)
    return path


async def _run_internal_engine(
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """
    Deterministic rules-engine only — no LLM calls.
    Passes --no-ai to the CLI to enforce the branch.
    """
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "observascore.cli", "assess",
        "--config", str(config_path),
        "--output", str(output_dir),
        "--no-ai",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
        "returncode": proc.returncode,
    }


async def _run_with_ai(
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """
    Full pipeline: deterministic rules engine + LLM gap analysis.
    The AI provider and key are embedded in the config.yaml written by
    _write_assess_config(), so the CLI picks them up automatically.
    """
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "observascore.cli", "assess",
        "--config", str(config_path),
        "--output", str(output_dir),
        "--ai",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
        "returncode": proc.returncode,
    }
