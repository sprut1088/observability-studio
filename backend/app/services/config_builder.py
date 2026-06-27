import os
import yaml
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse


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


def load_local_config() -> dict:
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_ai_model(model: str | None, provider: str | None = None) -> str | None:
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


def derive_splunk_urls(base_url: str) -> dict:
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


def build_runtime_config(payload: dict, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    config_path = workdir / f"config-{uuid4().hex}.yaml"

    sources = {}

    local_config = load_local_config()
    splunk_config = local_config.get("splunk", {})

    for tool in payload.get("tools", []):
        name = tool["name"]

        source_cfg = {
            "enabled": tool.get("enabled", True),
            "url": tool.get("url"),
        }

        if tool.get("api_key"):
            source_cfg["api_key"] = tool["api_key"]

        if tool.get("username"):
            source_cfg["username"] = tool["username"]

        if tool.get("password"):
            source_cfg["password"] = tool["password"]

        if name == "splunk":
            urls = derive_splunk_urls(tool.get("url", ""))

            source_cfg["splunk_base_url"] = tool.get("splunk_base_url") or urls["base"]
            source_cfg["splunk_mgmt_url"] = tool.get("splunk_mgmt_url") or urls["mgmt"]
            source_cfg["splunk_hec_url"] = tool.get("splunk_hec_url") or urls["hec"]
            source_cfg["splunk_hec_token"] = tool.get("splunk_hec_token") or tool.get("api_key")

            source_cfg["username"] = splunk_config.get("username")
            source_cfg["password"] = splunk_config.get("password")
            source_cfg["splunk_app"] = splunk_config.get("app", "search")
            source_cfg["splunk_verify_ssl"] = splunk_config.get("verify_ssl", False)

        sources[name] = source_cfg

    ai_raw = payload.get("ai") or {"enabled": False}
    ai_cfg = {k: v for k, v in ai_raw.items() if v is not None}

    if ai_cfg.get("enabled"):
        server_ai = local_config.get("ai", {})

        provider = (
            ai_cfg.get("provider")
            or server_ai.get("provider")
            or os.getenv("SLO_AI_PROVIDER")
            or os.getenv("AI_PROVIDER")
            or "anthropic"
        ).lower()

        ai_cfg["provider"] = provider

        if not ai_cfg.get("api_key"):
            api_key = (
                server_ai.get("api_key")
                or os.getenv("SLO_AI_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("AI_API_KEY")
            )
            if api_key:
                ai_cfg["api_key"] = api_key

        requested_model = (
            ai_cfg.get("model")
            or server_ai.get("model")
            or os.getenv("SLO_AI_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("AI_MODEL")
        )

        normalized_model = normalize_ai_model(requested_model, provider)
        if normalized_model:
            ai_cfg["model"] = normalized_model

    cfg = {
        "client": payload.get("client", {}),
        "sources": sources,
        "ai": ai_cfg,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return config_path