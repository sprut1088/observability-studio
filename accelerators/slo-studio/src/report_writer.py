from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jinja2 import Template


def write_reports(
    output_dir: Path,
    context: dict[str, Any],
    sloth_yaml: str,
) -> None:
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    json_path = reports_dir / "slo-studio-report.json"
    html_path = reports_dir / "slo-studio-report.html"
    yaml_path = reports_dir / "sloth-slos.yaml"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, default=_default)

    with yaml_path.open("w", encoding="utf-8") as f:
        f.write(sloth_yaml)

    template_path = Path(__file__).resolve().parents[1] / "templates" / "slo_report_html.jinja2"
    template = Template(template_path.read_text(encoding="utf-8"))
    html = template.render(**context)

    html_path.write_text(html, encoding="utf-8")


def _default(obj: Any) -> Any:
    try:
        return asdict(obj)
    except Exception:
        return str(obj)