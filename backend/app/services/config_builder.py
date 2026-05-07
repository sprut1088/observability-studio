import yaml
from pathlib import Path
from uuid import uuid4

def load_local_config() -> dict:
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

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

        # Splunk-specific fields
        if name == "splunk":
            if tool.get("splunk_base_url"):
                source_cfg["splunk_base_url"] = tool["splunk_base_url"]
            if tool.get("splunk_mgmt_url"):
                source_cfg["splunk_mgmt_url"] = tool["splunk_mgmt_url"]
            if tool.get("splunk_hec_url"):
                source_cfg["splunk_hec_url"] = tool["splunk_hec_url"]

            # Existing Auth Token field becomes Splunk HEC token
            source_cfg["splunk_hec_token"] = (
                tool.get("splunk_hec_token")
                or tool.get("api_key")
            )

            # Private values come from local config/config.yaml
            source_cfg["username"] = splunk_config.get("username")
            source_cfg["password"] = splunk_config.get("password")
            source_cfg["splunk_app"] = splunk_config.get("app", "search")
            source_cfg["splunk_verify_ssl"] = splunk_config.get("verify_ssl", False)

    ai_raw = payload.get("ai") or {"enabled": False}
    ai_cfg = {k: v for k, v in ai_raw.items() if v is not None}

    cfg = {
        "client": payload.get("client", {}),
        "sources": sources,
        "ai": ai_cfg,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return config_path