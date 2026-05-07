import yaml
from pathlib import Path
from uuid import uuid4

def build_runtime_config(payload: dict, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    config_path = workdir / f"config-{uuid4().hex}.yaml"

    sources = {}

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
            if tool.get("splunk_hec_token"):
                source_cfg["splunk_hec_token"] = tool["splunk_hec_token"]
            if tool.get("splunk_app"):
                source_cfg["splunk_app"] = tool["splunk_app"]

            source_cfg["splunk_verify_ssl"] = tool.get("splunk_verify_ssl", False)

        sources[name] = source_cfg

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