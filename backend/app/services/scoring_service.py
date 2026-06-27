"""
scoring_service.py
──────────────────
Runs the ObservaScore assessment pipeline for the Hub tile (/api/v1/assess).

Branch logic:
  use_ai=False  →  _run_internal_engine()
  use_ai=True   →  _run_with_ai()
"""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from backend.app.models.assessment import AssessmentRequest, AssessmentResponse


RUNTIME_DIR = Path("runtime")
BASE_URL = "http://10.235.21.132:8001"

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

ANTHROPIC_MODEL_ALIASES = {
    "claude-3-5-sonnet-latest": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-5-sonnet": DEFAULT_ANTHROPIC_MODEL,
    "claude-3.5-sonnet": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-5-sonnet-20240620": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-5-sonnet-20241022": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-7-sonnet-20250219": DEFAULT_ANTHROPIC_MODEL,
    "claude-sonnet-4": DEFAULT_ANTHROPIC_MODEL,
    "claude-sonnet-4-20250514": DEFAULT_ANTHROPIC_MODEL,
}


def _normalize_ai_model(model: str | None, provider: str | None = None) -> str | None:
    provider = (provider or "anthropic").lower()
    raw = (model or "").strip()

    if provider in {"anthropic", "claude"}:
        if not raw:
            raw = (
                os.getenv("SLO_AI_MODEL")
                or os.getenv("ANTHROPIC_MODEL")
                or os.getenv("AI_MODEL")
                or DEFAULT_ANTHROPIC_MODEL
            )
        return ANTHROPIC_MODEL_ALIASES.get(raw, raw)

    return raw or model


def _build_runtime_urls(file_path: Path) -> tuple[str, str]:
    rel = file_path.relative_to(RUNTIME_DIR)
    rel_path = rel.as_posix()
    return (
        f"{BASE_URL}/api/preview/runtime/{rel_path}",
        f"{BASE_URL}/api/download/runtime/{rel_path}",
    )


async def run_scoring(req: AssessmentRequest) -> AssessmentResponse:
    run_id = uuid.uuid4().hex
    workdir = RUNTIME_DIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)

    config_path = _write_assess_config(req, workdir)
    output_dir = workdir / "reports"
    output_dir.mkdir(exist_ok=True)

    if req.use_ai:
        result = await _run_with_ai(config_path, output_dir)
    else:
        result = await _run_internal_engine(config_path, output_dir)

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


def _write_assess_config(req: AssessmentRequest, workdir: Path) -> Path:
    tool_key = req.tool_source.lower()
    source: dict[str, Any] = {"enabled": True, "url": req.api_endpoint}

    if req.auth_token:
        source["api_key"] = req.auth_token

    ai_cfg: dict[str, Any] = {"enabled": req.use_ai}

    if req.use_ai:
        provider = (req.ai_provider or "anthropic").lower()
        ai_cfg["provider"] = provider

        server_ai = {}
        server_cfg_path = Path("config/config.yaml")
        if server_cfg_path.exists():
            with open(server_cfg_path, encoding="utf-8") as fh:
                server_cfg = yaml.safe_load(fh) or {}
            server_ai = server_cfg.get("ai", {}) or {}

        api_key = (
            req.ai_api_key
            or server_ai.get("api_key")
            or os.getenv("SLO_AI_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("AI_API_KEY")
        )

        if api_key:
            ai_cfg["api_key"] = api_key

        model = (
            server_ai.get("model")
            or os.getenv("SLO_AI_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("AI_MODEL")
        )

        normalized_model = _normalize_ai_model(model, provider)
        if normalized_model:
            ai_cfg["model"] = normalized_model

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
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(config, fh, default_flow_style=False)

    return path


async def _run_internal_engine(
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
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