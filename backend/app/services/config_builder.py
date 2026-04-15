import yaml
from pathlib import Path
from uuid import uuid4

def build_runtime_config(payload: dict, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    config_path = workdir / f"config-{uuid4().hex}.yaml"

    sources = {}
    for tool in payload.get("tools", []):
        if tool.get("name") == "splunk":
            # MVP placeholder: not wired yet
            continue

        sources[tool["name"]] = {
            "enabled": tool.get("enabled", True),
            "url": tool.get("url"),
        }

        if tool.get("api_key"):
            sources[tool["name"]]["api_key"] = tool["api_key"]
        if tool.get("username"):
            sources[tool["name"]]["username"] = tool["username"]
        if tool.get("password"):
            sources[tool["name"]]["password"] = tool["password"]

    cfg = {
        "client": payload.get("client", {}),
        "sources": sources,
        "ai": payload.get("ai", {"enabled": False}),
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return config_path