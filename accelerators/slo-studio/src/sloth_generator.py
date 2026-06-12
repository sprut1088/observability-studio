from __future__ import annotations

from models import ServiceSLO


def generate_sloth_yaml(slos: list[ServiceSLO]) -> str:
    lines: list[str] = []

    lines.append("version: prometheus/v1")
    lines.append("service: observability-studio-generated")
    lines.append("slos:")

    for slo in slos:
        slo_name = f"{slo.service}-{slo.sli_type}".replace("_", "-").lower()

        lines.append(f"  - name: {slo_name}")
        lines.append(f"    objective: {slo.objective}")
        lines.append(f"    description: {quote(slo.description)}")
        lines.append("    sli:")
        lines.append("      events:")
        lines.append(f"        error_query: |")
        lines.append(f"          ({slo.query_total}) - ({slo.query_good})")
        lines.append(f"        total_query: |")
        lines.append(f"          {slo.query_total}")
        lines.append("    alerting:")
        lines.append(f"      name: {slo.service.title().replace('-', '')}{slo.sli_type.title()}ErrorBudgetBurn")
        lines.append("      labels:")
        lines.append("        category: availability")
        lines.append("      annotations:")
        lines.append(f"        summary: {quote(slo.description)}")
        lines.append("      page_alert:")
        lines.append("        labels:")
        lines.append("          severity: critical")
        lines.append("      ticket_alert:")
        lines.append("        labels:")
        lines.append("          severity: warning")

    return "\n".join(lines) + "\n"


def quote(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'