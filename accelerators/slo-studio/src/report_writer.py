from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from jinja2 import Template


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, list):
        return [to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: to_jsonable(value) for key, value in obj.items()}
    return obj


def write_reports(output_dir: Path, context: dict[str, Any], sloth_yaml: str) -> None:
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    json_path = reports_dir / "slo-studio-report.json"
    html_path = reports_dir / "slo-studio-report.html"
    yaml_path = reports_dir / "sloth-slos.yaml"

    json_context = to_jsonable(context)

    json_path.write_text(json.dumps(json_context, indent=2), encoding="utf-8")
    yaml_path.write_text(sloth_yaml, encoding="utf-8")

    template_path = Path(__file__).resolve().parents[1] / "templates" / "slo_report_html.jinja2"
    template = Template(template_path.read_text(encoding="utf-8"))
    html = template.render(**json_context)

    html_path.write_text(html, encoding="utf-8")