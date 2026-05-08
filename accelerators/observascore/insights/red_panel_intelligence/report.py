from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path

from observascore.insights.red_panel_intelligence.models import RedPanelIntelligenceResult

logger = logging.getLogger(__name__)


def _status_badge_class(status: str) -> str:
    if status == "complete":
        return "badge-complete"
    if status == "partial":
        return "badge-partial"
    if status == "weak":
        return "badge-weak"
    return "badge-non-operational"


def _render_dashboard_tool_coverage(result: RedPanelIntelligenceResult) -> str:
    if not result.dashboard_coverage_by_tool:
        return "<p class='muted'>No dashboard tool coverage data available.</p>"

    rows = []
    for tool, data in sorted(result.dashboard_coverage_by_tool.items()):
        rows.append(
            """
            <tr>
              <td>{tool}</td>
              <td>{dashboard_count}</td>
              <td>{avg_red_score:.2f}</td>
              <td>{complete_dashboards}</td>
              <td>{partial_dashboards}</td>
              <td>{weak_dashboards}</td>
              <td>{non_operational_dashboards}</td>
            </tr>
            """.format(
                tool=escape(tool),
                dashboard_count=int(data.get("dashboard_count", 0)),
                avg_red_score=float(data.get("avg_red_score", 0.0)),
                complete_dashboards=int(data.get("complete_dashboards", 0)),
                partial_dashboards=int(data.get("partial_dashboards", 0)),
                weak_dashboards=int(data.get("weak_dashboards", 0)),
                non_operational_dashboards=int(data.get("non_operational_dashboards", 0)),
            )
        )

    return """
    <table>
      <thead>
        <tr>
          <th>Tool</th>
          <th>Dashboards</th>
          <th>Avg RED Score</th>
          <th>Complete</th>
          <th>Partial</th>
          <th>Weak</th>
          <th>Non-operational</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    """.format(rows="".join(rows))


def _render_red_matrix(result: RedPanelIntelligenceResult) -> str:
    if not result.dashboard_analyses:
        return "<p class='muted'>No dashboards available for RED matrix analysis.</p>"

    rows = []
    for analysis in result.dashboard_analyses:
        rows.append(
            """
            <tr>
              <td>{tool}</td>
              <td>{title}</td>
              <td>{rate}</td>
              <td>{errors}</td>
              <td>{duration}</td>
              <td>{score}</td>
              <td><span class='badge {badge}'>{status}</span></td>
            </tr>
            """.format(
                tool=escape(analysis.source_tool),
                title=escape(analysis.dashboard_title),
                rate="Yes" if analysis.rate_present else "No",
                errors="Yes" if analysis.errors_present else "No",
                duration="Yes" if analysis.duration_present else "No",
                score=analysis.red_score,
                badge=_status_badge_class(analysis.status),
                status=escape(analysis.status),
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
          <th>RED Score</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def _render_weak_dashboards(result: RedPanelIntelligenceResult) -> str:
    weak_dashboards = [
        analysis
        for analysis in result.dashboard_analyses
        if analysis.status in {"weak", "non_operational"}
    ]

    if not weak_dashboards:
        return "<p class='muted'>No weak or non-operational dashboards detected.</p>"

    rows = []
    for analysis in weak_dashboards:
        rows.append(
            """
            <tr>
              <td>{tool}</td>
              <td>{title}</td>
              <td>{score}</td>
              <td><span class='badge {badge}'>{status}</span></td>
              <td>{recommendations}</td>
            </tr>
            """.format(
                tool=escape(analysis.source_tool),
                title=escape(analysis.dashboard_title),
                score=analysis.red_score,
                badge=_status_badge_class(analysis.status),
                status=escape(analysis.status),
                recommendations=escape("; ".join(analysis.recommendations) or "None"),
            )
        )

    return """
    <table>
      <thead>
        <tr>
          <th>Tool</th>
          <th>Dashboard</th>
          <th>RED Score</th>
          <th>Status</th>
          <th>Recommendations</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def _render_recommendations(result: RedPanelIntelligenceResult) -> str:
    if not result.top_recommendations:
        return "<p class='muted'>No recommendations generated.</p>"

    items = "".join(f"<li>{escape(item)}</li>" for item in result.top_recommendations)
    return f"<ul>{items}</ul>"


def _render_panel_evidence(result: RedPanelIntelligenceResult) -> str:
    rows = []
    for analysis in result.dashboard_analyses:
        for evidence in analysis.evidence:
            rows.append(
                """
                <tr>
                  <td>{tool}</td>
                  <td>{dashboard}</td>
                  <td>{category}</td>
                  <td>{source}</td>
                  <td>{keyword}</td>
                  <td>{panel_title}</td>
                  <td>{query}</td>
                </tr>
                """.format(
                    tool=escape(evidence.source_tool),
                    dashboard=escape(evidence.dashboard_title),
                    category=escape(evidence.category),
                    source=escape(evidence.source),
                    keyword=escape(evidence.matched_keyword),
                    panel_title=escape(evidence.panel_title or "-"),
                    query=escape(evidence.query or "-"),
                )
            )

    if not rows:
        return "<p class='muted'>No panel evidence matched RED heuristics.</p>"

    return """
    <table>
      <thead>
        <tr>
          <th>Tool</th>
          <th>Dashboard</th>
          <th>Category</th>
          <th>Evidence Source</th>
          <th>Matched Keyword</th>
          <th>Panel</th>
          <th>Query</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def generate_red_panel_intelligence_report(
    result: RedPanelIntelligenceResult,
    output_dir: Path,
    filename: str = "red-intelligence-report.html",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    extraction_errors_block = ""
    if result.extraction_errors:
        errors = "".join(f"<li>{escape(err)}</li>" for err in result.extraction_errors)
        extraction_errors_block = f"<section><h2>Extraction Errors</h2><ul>{errors}</ul></section>"

    no_data_message = ""
    if result.no_dashboards_found:
        no_data_message += "<p class='warning'>No dashboards were discovered from the selected tools.</p>"
    elif result.no_panels_found:
        no_data_message += "<p class='warning'>Dashboards were discovered, but panel details were unavailable or unsupported.</p>"

    html = f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>RED Panel Intelligence</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #f5f7fb; color: #1f2937; margin: 0; padding: 24px; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; margin-bottom: 16px; }}
    h1 {{ margin: 0 0 8px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    .muted {{ color: #6b7280; margin: 0; }}
    .warning {{ background: #fff7ed; color: #9a3412; border: 1px solid #fdba74; border-radius: 8px; padding: 12px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-top: 12px; }}
    .kpi {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; }}
    .kpi-label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.04em; }}
    .kpi-value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f4f6; }}
    ul {{ margin: 0; padding-left: 20px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; text-transform: capitalize; }}
    .badge-complete {{ background: #dcfce7; color: #166534; }}
    .badge-partial {{ background: #fef9c3; color: #854d0e; }}
    .badge-weak {{ background: #ffedd5; color: #9a3412; }}
    .badge-non-operational {{ background: #fee2e2; color: #991b1b; }}
  </style>
</head>
<body>
  <div class='container'>
    <section class='card'>
      <h1>RED Panel Intelligence</h1>
      <p class='muted'>Rate, Errors, Duration dashboard-quality analysis based on normalized dashboard and panel metadata.</p>
      {no_data_message}
      <div class='kpis'>
        <div class='kpi'><div class='kpi-label'>Executive RED Score</div><div class='kpi-value'>{result.overall_red_score:.2f}</div></div>
        <div class='kpi'><div class='kpi-label'>Total Dashboards</div><div class='kpi-value'>{result.total_dashboards}</div></div>
        <div class='kpi'><div class='kpi-label'>Complete</div><div class='kpi-value'>{result.complete_dashboards}</div></div>
        <div class='kpi'><div class='kpi-label'>Partial</div><div class='kpi-value'>{result.partial_dashboards}</div></div>
        <div class='kpi'><div class='kpi-label'>Weak</div><div class='kpi-value'>{result.weak_dashboards}</div></div>
        <div class='kpi'><div class='kpi-label'>Non-operational</div><div class='kpi-value'>{result.non_operational_dashboards}</div></div>
      </div>
    </section>

    <section class='card'>
      <h2>Dashboard Coverage by Tool</h2>
      {_render_dashboard_tool_coverage(result)}
    </section>

    <section class='card'>
      <h2>RED Matrix</h2>
      {_render_red_matrix(result)}
    </section>

    <section class='card'>
      <h2>Weak / Non-operational Dashboards</h2>
      {_render_weak_dashboards(result)}
    </section>

    <section class='card'>
      <h2>Recommendations</h2>
      {_render_recommendations(result)}
    </section>

    <section class='card'>
      <h2>Panel Evidence</h2>
      {_render_panel_evidence(result)}
    </section>

    {extraction_errors_block}
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
