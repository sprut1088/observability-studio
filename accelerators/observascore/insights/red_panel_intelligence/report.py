from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path

from observascore.insights.red_panel_intelligence.models import RedPanelIntelligenceResult

logger = logging.getLogger(__name__)


def _status_class(status: str) -> str:
    if status == "complete":
        return "status-complete"
    if status == "partial":
        return "status-partial"
    if status == "weak":
        return "status-weak"
    return "status-blind"


def _render_services(result: RedPanelIntelligenceResult) -> str:
    if not result.service_coverage:
        return "<p class='muted'>No services available for analysis.</p>"

    rows: list[str] = []
    for svc in result.service_coverage:
        rows.append(
            """
            <tr>
              <td>{service}</td>
              <td>{source}</td>
              <td>{rate}</td>
              <td>{errors}</td>
              <td>{duration}</td>
              <td>{score}</td>
              <td><span class='pill {status_class}'>{status}</span></td>
            </tr>
            """.format(
                service=escape(svc.service),
                source="auto-discovered" if svc.auto_discovered else "canonical",
                rate="Yes" if svc.rate.found else "No",
                errors="Yes" if svc.errors.found else "No",
                duration="Yes" if svc.duration.found else "No",
                score=svc.red_score,
                status_class=_status_class(svc.status),
                status=escape(svc.status),
            )
        )

    return """
    <table>
      <thead>
        <tr>
          <th>Service</th>
          <th>Source</th>
          <th>Rate</th>
          <th>Errors</th>
          <th>Duration</th>
          <th>Score</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def _render_evidence(result: RedPanelIntelligenceResult) -> str:
    rows: list[str] = []
    for svc in result.service_coverage:
        for group in (svc.rate, svc.errors, svc.duration):
            for ev in group.evidence:
                rows.append(
                    """
                    <tr>
                      <td>{service}</td>
                      <td>{category}</td>
                      <td>{tool}</td>
                      <td>{dashboard}</td>
                      <td>{panel}</td>
                      <td>{source}</td>
                      <td>{keyword}</td>
                      <td>{query}</td>
                    </tr>
                    """.format(
                        service=escape(ev.service),
                        category=escape(ev.category),
                        tool=escape(ev.source_tool),
                        dashboard=escape(ev.dashboard_title),
                        panel=escape(ev.panel_title or "-"),
                        source=escape(ev.source),
                        keyword=escape(ev.matched_keyword),
                        query=escape(ev.query or "-"),
                    )
                )

    if not rows:
        return "<p class='muted'>No RED evidence mapped to scoped services.</p>"

    return """
    <table>
      <thead>
        <tr>
          <th>Service</th>
          <th>Signal</th>
          <th>Tool</th>
          <th>Dashboard</th>
          <th>Panel</th>
          <th>Evidence Source</th>
          <th>Keyword</th>
          <th>Query</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def _render_gaps(result: RedPanelIntelligenceResult) -> str:
    gap_items: list[str] = []
    for svc in result.service_coverage:
        if svc.status == "complete":
            continue
        recs = "; ".join(svc.recommendations) if svc.recommendations else "No recommendations generated"
        gap_items.append(f"<li><strong>{escape(svc.service)}</strong>: {escape(recs)}</li>")

    if not gap_items:
        return "<p class='muted'>All services have complete RED coverage.</p>"

    return f"<ul>{''.join(gap_items)}</ul>"


def _render_dashboard_appendix(result: RedPanelIntelligenceResult) -> str:
    if not result.dashboard_appendix:
        return "<p class='muted'>No dashboard appendix data available.</p>"

    rows: list[str] = []
    for item in result.dashboard_appendix:
        rows.append(
            """
            <tr>
              <td>{tool}</td>
              <td>{dashboard}</td>
              <td>{rate}</td>
              <td>{errors}</td>
              <td>{duration}</td>
              <td>{score}</td>
              <td>{status}</td>
            </tr>
            """.format(
                tool=escape(item.source_tool),
                dashboard=escape(item.dashboard_title),
                rate="Yes" if item.rate_present else "No",
                errors="Yes" if item.errors_present else "No",
                duration="Yes" if item.duration_present else "No",
                score=item.red_score,
                status=escape(item.status),
            )
        )

    return """
    <table>
      <thead>
        <tr>
          <th>Tool</th>
          <th>Dashboard</th>
          <th>Rate</th>
          <th>Errors</th>
          <th>Duration</th>
          <th>Score</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def _render_notes(result: RedPanelIntelligenceResult) -> str:
    notes = list(result.guidance)
    if result.fallback_to_auto_discovery:
        notes.append("Fallback mode active: no canonical services were provided.")
    if not notes:
        return "<p class='muted'>No warnings or guidance.</p>"
    return "<ul>" + "".join(f"<li>{escape(note)}</li>" for note in notes) + "</ul>"


def generate_red_panel_intelligence_report(
    result: RedPanelIntelligenceResult,
    output_dir: Path,
    filename: str = "red-intelligence-report.html",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    html = f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>RED Coverage Intelligence</title>
  <style>
    :root {{
      --bg: #f6f8fc;
      --ink: #1f2937;
      --muted: #6b7280;
      --card: #ffffff;
      --line: #e5e7eb;
      --accent: #f43f5e;
      --ok: #15803d;
      --warn: #a16207;
      --danger: #b91c1c;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", Tahoma, sans-serif; color: var(--ink); background: radial-gradient(circle at 20% 0%, #ffe4ec, transparent 35%), var(--bg); }}
    .page {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero {{ position: sticky; top: 0; z-index: 5; background: rgba(255,255,255,0.92); backdrop-filter: blur(6px); border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 16px; }}
    .hero h1 {{ margin: 0 0 8px; font-size: 26px; }}
    .hero p {{ margin: 0; color: var(--muted); }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-top: 14px; }}
    .kpi {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px; }}
    .kpi label {{ display: block; font-size: 11px; text-transform: uppercase; color: var(--muted); letter-spacing: .05em; }}
    .kpi strong {{ font-size: 24px; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; margin-bottom: 12px; }}
    h2 {{ margin: 0 0 10px; font-size: 19px; }}
    .muted {{ color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border: 1px solid var(--line); padding: 7px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f5fb; position: sticky; top: 68px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    .pill {{ border-radius: 999px; padding: 2px 8px; font-size: 12px; text-transform: capitalize; }}
    .status-complete {{ background: #dcfce7; color: var(--ok); }}
    .status-partial {{ background: #fef3c7; color: var(--warn); }}
    .status-weak {{ background: #ffedd5; color: #9a3412; }}
    .status-blind {{ background: #fee2e2; color: var(--danger); }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    @media (max-width: 860px) {{
      .page {{ padding: 14px; }}
      .hero {{ position: static; }}
      th {{ position: static; }}
      .two-col {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class='page'>
    <section class='hero'>
      <h1>RED Coverage Intelligence</h1>
      <p>Application: {escape(result.application_name)} | Environment: {escape(result.environment)}</p>
      <div class='kpis'>
        <div class='kpi'><label>Overall RED Coverage</label><strong>{result.overall_red_coverage_score:.2f}</strong></div>
        <div class='kpi'><label>Services Assessed</label><strong>{result.services_assessed}</strong></div>
        <div class='kpi'><label>Fully Covered</label><strong>{result.fully_covered_services}</strong></div>
        <div class='kpi'><label>Partial</label><strong>{result.partial_services}</strong></div>
        <div class='kpi'><label>Blind Spots</label><strong>{result.blind_services}</strong></div>
      </div>
    </section>

    <section class='card'>
      <h2>Scope And Notes</h2>
      <div class='two-col'>
        <div>
          <p class='muted'>Canonical services supplied by caller.</p>
          <ul>{''.join(f'<li>{escape(item)}</li>' for item in result.canonical_services) or '<li>None</li>'}</ul>
        </div>
        <div>
          <p class='muted'>Guidance and warnings.</p>
          {_render_notes(result)}
        </div>
      </div>
    </section>

    <section class='card'>
      <h2>Service RED Coverage Matrix</h2>
      {_render_services(result)}
    </section>

    <section class='card'>
      <h2>Critical Blind Spots And Fix Plan</h2>
      {_render_gaps(result)}
    </section>

    <section class='card'>
      <h2>Evidence Mapping</h2>
      {_render_evidence(result)}
    </section>

    <section class='card'>
      <h2>Dashboard Appendix</h2>
      {_render_dashboard_appendix(result)}
    </section>
  </div>
</body>
</html>
"""

    output_path.write_text(html, encoding="utf-8")
    logger.info("Generated RED Panel Intelligence HTML report at %s", output_path)
    return output_path


def write_red_panel_intelligence_outputs(
    result: RedPanelIntelligenceResult,
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = generate_red_panel_intelligence_report(result=result, output_dir=output_dir)

    json_path = output_dir / "red-intelligence.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    logger.info("Generated RED Panel Intelligence JSON report at %s", json_path)

    return {"html": html_path, "json": json_path}
