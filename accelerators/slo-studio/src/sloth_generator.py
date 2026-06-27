from __future__ import annotations

from models import SLORecommendation


def quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def generate_sloth_yaml(recommendations: list[SLORecommendation]) -> str:
    lines: list[str] = []
    lines.append("version: prometheus/v1")
    lines.append("service: observability-studio-generated")
    lines.append("slos:")

    for rec in recommendations:
        name = rec.name.replace("_", "-").lower()

        lines.append(f"  - name: {name}")
        lines.append(f"    objective: {rec.objective}")
        lines.append(f"    description: {quote(rec.description)}")
        lines.append("    sli:")
        lines.append("      events:")
        lines.append("        error_query: |")
        lines.append(f"          ({rec.total_query}) - ({rec.good_query})")
        lines.append("        total_query: |")
        lines.append(f"          {rec.total_query}")
        lines.append("    alerting:")
        lines.append(f"      name: {name.title().replace('-', '')}ErrorBudgetBurn")
        lines.append("      labels:")
        lines.append(f"        service: {quote(rec.service)}")
        lines.append(f"        slo_type: {quote(rec.sli_type)}")
        lines.append("      annotations:")
        lines.append(f"        summary: {quote(rec.description)}")
        lines.append(f"        rationale: {quote(rec.rationale[:500])}")

        if rec.page_alert:
            lines.append("      page_alert:")
            lines.append("        labels:")
            lines.append("          severity: critical")

        lines.append("      ticket_alert:")
        lines.append("        labels:")
        lines.append("          severity: warning")

    return "\n".join(lines) + "\n"