"""
crawler_service.py
──────────────────
Handles tool-specific connectivity validation and data extraction for the
ObsCrawl Hub tile (/api/v1/validate, /api/v1/crawl).

Two-phase design:
  1. validate_connection()  — lightweight HTTP probe using the tool's
     declared health endpoint from tools.yaml.
  2. run_crawl()            — full extraction via the observascore CLI,
     with a pandas-based fallback Excel if the adapter fails.
"""

import asyncio
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml
from urllib.parse import urlparse

from backend.app.models.connection import ConnectionSchema, ConnectionResponse

# ── Paths ────────────────────────────────────────────────────────────────────
RUNTIME_DIR = Path("runtime")
_TOOLS_YAML = Path(__file__).parent.parent / "config" / "tools.yaml"
BASE_URL = "http://10.235.21.132:8001"

def load_local_config() -> dict[str, Any]:
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
    
def derive_splunk_urls(base_url: str) -> dict[str, str]:
    parsed = urlparse(base_url)
    hostname = parsed.hostname

    if not hostname:
        return {
            "base": base_url.rstrip("/"),
            "mgmt": base_url.rstrip("/"),
            "hec": base_url.rstrip("/"),
        }

    return {
        "base": f"http://{hostname}:8000",
        "mgmt": f"https://{hostname}:8089",
        "hec": f"http://{hostname}:8088",
    }


# ── Tool catalogue (loaded once at import) ────────────────────────────────────
def _load_tools() -> dict[str, Any]:
    if _TOOLS_YAML.exists():
        with open(_TOOLS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("tools", {})
    return {}


TOOLS: dict[str, Any] = _load_tools()


# ── Public API ────────────────────────────────────────────────────────────────

async def validate_connection(conn: ConnectionSchema) -> ConnectionResponse:
    """
    Probe the tool's declared health endpoint.
    Returns latency and a human-readable reachability message.
    """
    tool_cfg = TOOLS.get(conn.tool_name.lower(), {})
    defaults = {"timeout_seconds": 15}

    health_path = tool_cfg.get("health_endpoint", "/")
    timeout = tool_cfg.get("timeout_seconds", defaults["timeout_seconds"])
    url = conn.base_url.rstrip("/") + health_path

    headers: dict[str, str] = {}
    if conn.auth_token:
        headers["Authorization"] = f"Bearer {conn.auth_token}"

    start = time.monotonic()
    try:
        resp = await asyncio.to_thread(
            requests.get, url,
            headers=headers,
            timeout=timeout,
            verify=False,          # lab/self-signed certs
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        if resp.status_code < 400:
            reachable = True
            message = f"HTTP {resp.status_code} — {tool_cfg.get('display_name', conn.tool_name)} is reachable"
        elif resp.status_code < 500:
            reachable = True
            message = f"HTTP {resp.status_code} — endpoint responded (auth may be required)"
        else:
            reachable = False
            message = f"HTTP {resp.status_code} — server error"

    except requests.exceptions.ConnectionError:
        latency_ms = None
        reachable = False
        message = "Connection refused — verify the service is running and the URL is correct"
    except requests.exceptions.Timeout:
        latency_ms = None
        reachable = False
        message = f"Timed out after {timeout}s — service may be overloaded or unreachable"
    except Exception as exc:  # noqa: BLE001
        latency_ms = None
        reachable = False
        message = str(exc)

    return ConnectionResponse(
        tool_name=conn.tool_name,
        reachable=reachable,
        message=message,
        latency_ms=latency_ms,
    )


async def run_crawl(conn: ConnectionSchema) -> dict[str, Any]:
    """
    Full extraction for a single tool, exported to Excel.

    Strategy:
      1. Build a minimal observascore config.yaml for the tool.
      2. Invoke `python -m observascore.cli export` as a subprocess.
      3. If the CLI produces an .xlsx, return its download URL.
      4. On failure, fall back to a pandas-generated skeleton Excel.
    """
    run_id = uuid.uuid4().hex
    workdir = RUNTIME_DIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)

    config_path = _write_crawl_config(conn, workdir)
    output_dir = workdir / "exports"
    output_dir.mkdir(exist_ok=True)

    # ── Run CLI export ──────────────────────────────────────────────────────
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "observascore.cli", "export",
        "--config", str(config_path),
        "--output", str(output_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    # ── Locate output ───────────────────────────────────────────────────────
    xlsx_files = sorted(
        output_dir.glob("*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if xlsx_files:
        xlsx_path = xlsx_files[0]
    else:
        # CLI extraction failed or adapter not available — generate a
        # pandas-based skeleton so the user always gets a file.
        xlsx_path = await _pandas_fallback_excel(conn, output_dir, stderr.decode())

    rel = xlsx_path.relative_to(RUNTIME_DIR)
    download_url = f"{BASE_URL}/api/download/runtime/{rel.as_posix()}"

    return {
        "success": True,
        "message": f"Crawl complete — {conn.tool_name}",
        "download_url": download_url,
        "run_id": run_id,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_crawl_config(conn: ConnectionSchema, workdir: Path) -> Path:
    """Emit a minimal observascore config.yaml for a single tool."""
    tool_key = conn.tool_name.lower()
    #source: dict[str, Any] = {"enabled": True, "url": conn.base_url}
    #if conn.auth_token:
    #    source["api_key"] = conn.auth_token

    local_config = load_local_config()
    splunk_config = local_config.get("splunk", {})

    source: dict[str, Any] = {
        "enabled": True,
        "url": conn.base_url,
    }

    if conn.auth_token:
        source["api_key"] = conn.auth_token
    
    if tool_key == "splunk":
        urls = derive_splunk_urls(conn.base_url)

        source["splunk_base_url"] = conn.splunk_base_url or urls["base"]
        source["splunk_mgmt_url"] = conn.splunk_mgmt_url or urls["mgmt"]
        source["splunk_hec_url"] = conn.splunk_hec_url or urls["hec"]

        source["splunk_hec_token"] = conn.splunk_hec_token or conn.auth_token
        source["username"] = splunk_config.get("username")
        source["password"] = splunk_config.get("password")
        source["splunk_app"] = splunk_config.get("app", "search")
        source["splunk_verify_ssl"] = splunk_config.get("verify_ssl", False)


    config = {
        "client": {
            "name": conn.tool_name.capitalize(),
            "environment": "hub-crawl",
        },
        "sources": {tool_key: source},
        "ai": {"enabled": False},
    }
    path = workdir / "config.yaml"
    with open(path, "w") as fh:
        yaml.dump(config, fh, default_flow_style=False)
    return path


async def _pandas_fallback_excel(
    conn: ConnectionSchema,
    output_dir: Path,
    cli_stderr: str,
) -> Path:
    """
    Pandas-backed skeleton Excel — produced when the CLI adapter cannot
    extract live data (e.g. tool is unreachable or adapter not registered).
    Sheets: Summary | Connection Info | Extraction Log
    """
    import pandas as pd  # local import — optional dep

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M")
    xlsx_path = output_dir / f"obscrawl-{conn.tool_name}-{ts}.xlsx"

    tool_cfg = TOOLS.get(conn.tool_name.lower(), {})

    with pd.ExcelWriter(str(xlsx_path), engine="openpyxl") as writer:
        # Sheet 1 — Summary
        pd.DataFrame([{
            "Tool":             tool_cfg.get("display_name", conn.tool_name),
            "Category":         tool_cfg.get("category", "—"),
            "Endpoint":         conn.base_url,
            "Auth Configured":  "Yes" if conn.auth_token else "No",
            "Crawl Timestamp":  datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "Status":           "Partial — live extraction unavailable",
        }]).to_excel(writer, sheet_name="Summary", index=False)

        # Sheet 2 — Connection Info
        caps = ", ".join(tool_cfg.get("capabilities", []))
        pd.DataFrame([
            {"Field": "Tool Name",    "Value": conn.tool_name},
            {"Field": "Display Name", "Value": tool_cfg.get("display_name", conn.tool_name)},
            {"Field": "Base URL",     "Value": conn.base_url},
            {"Field": "Category",     "Value": tool_cfg.get("category", "—")},
            {"Field": "Capabilities", "Value": caps or "—"},
            {"Field": "Health Path",  "Value": tool_cfg.get("health_endpoint", "/")},
        ]).to_excel(writer, sheet_name="Connection Info", index=False)

        # Sheet 3 — Extraction Log
        pd.DataFrame([{
            "Timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "Step":      "CLI export",
            "Status":    "failed" if cli_stderr else "skipped",
            "Detail":    cli_stderr[:500] if cli_stderr else "No output",
        }]).to_excel(writer, sheet_name="Extraction Log", index=False)

    return xlsx_path
