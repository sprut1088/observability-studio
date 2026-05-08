from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path

from observascore.insights.incident_simulator.models import IncidentSimulationResult

logger = logging.getLogger(__name__)


def _status_badge(status: str) -> tuple[str, str]:
    if status == "pass":
        return "✓", "#15803d"
    if status == "warn":
        return "⚠", "#ea580c"
    return "✗", "#991b1b"


def _render_readiness_gauge(score: float, width: int = 120) -> str:
    pct = min(100, max(0, score))
    fill_width = int((pct / 100) * width)
    return f'<div style="width:{width}px;height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden;"><div style="width:{fill_width}px;height:100%;background:#f43f5e;transition:all 0.3s;"></div></div>'


def _render_journey_stage(name: str, score: float, status: str) -> str:
    icon_val, color = _status_badge(status)
    return f"""
    <div style="flex:1;text-align:center;">
      <div style="font-size:28px;margin-bottom:6px;color:{color};">{icon_val}</div>
      <div style="font-weight:600;font-size:13px;">{name}</div>
      <div style="font-size:20px;font-weight:700;color:{color};margin-top:4px;">{score:.0f}</div>
    </div>
    """


def _render_check_row(check: dict[str, any]) -> str:
    icon, color = _status_badge(check["status"])
    evidence_count = len(check.get("evidence", []))
    return f"""
    <tr>
      <td style="padding:10px;border-bottom:1px solid #e5e7eb;">
        <span style="color:{color};font-weight:700;font-size:14px;">{icon}</span>
      </td>
      <td style="padding:10px;border-bottom:1px solid #e5e7eb;">
        <div style="font-weight:600;">{escape(check['name'])}</div>
        <div style="font-size:12px;color:#6b7280;margin-top:3px;">{escape(check['explanation'])}</div>
      </td>
      <td style="padding:10px;border-bottom:1px solid #e5e7eb;text-align:right;">
        <div style="font-weight:700;font-size:16px;">{check['score']}</div>
      </td>
      <td style="padding:10px;border-bottom:1px solid #e5e7eb;text-align:center;">
        <div style="font-size:12px;color:#6b7280;">{evidence_count} item(s)</div>
      </td>
    </tr>
    """


def _render_evidence_section(category: str, evidence_list: list[dict]) -> str:
    if not evidence_list:
        return f"<p style='color:#6b7280;'>No evidence found for {category}.</p>"

    rows = "".join(
        f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #f3f4f6;">{escape(ev['source_tool'])}</td>
          <td style="padding:8px;border-bottom:1px solid #f3f4f6;">{escape(ev['object_type'])}</td>
          <td style="padding:8px;border-bottom:1px solid #f3f4f6;">{escape(ev['object_name'][:50])}</td>
          <td style="padding:8px;border-bottom:1px solid #f3f4f6;text-align:right;">
            <span style="display:inline-block;background:#ecfdf5;color:#15803d;padding:2px 6px;border-radius:3px;font-size:11px;">
              {int(ev['confidence'] * 100)}%
            </span>
          </td>
        </tr>
        """
        for ev in evidence_list
    )
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead>
        <tr style="background:#f9fafb;">
          <th style="padding:8px;text-align:left;font-weight:600;">Source</th>
          <th style="padding:8px;text-align:left;font-weight:600;">Type</th>
          <th style="padding:8px;text-align:left;font-weight:600;">Name</th>
          <th style="padding:8px;text-align:right;font-weight:600;">Confidence</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """


def _render_recommendations(recommendations: list[str]) -> str:
    if not recommendations:
        return "<p style='color:#6b7280;'>All checks passed. No recommendations.</p>"
    items = "".join(f"<li style='margin-bottom:8px;'>{escape(item)}</li>" for item in recommendations)
    return f"<ol style='margin:0;padding-left:20px;'>{items}</ol>"


def generate_incident_report(
    result: IncidentSimulationResult,
    output_dir: Path,
    filename: str = "incident-simulation-report.html",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    result_dict = result.to_dict()

    journey_stages = _render_journey_stage("Detect", result.detection_score, "pass" if result.detection_score >= 70 else ("warn" if result.detection_score >= 40 else "fail"))
    journey_stages += _render_journey_stage("Visualize", result.visibility_score, "pass" if result.visibility_score >= 70 else ("warn" if result.visibility_score >= 40 else "fail"))
    journey_stages += _render_journey_stage("Diagnose", result.diagnosis_score, "pass" if result.diagnosis_score >= 70 else ("warn" if result.diagnosis_score >= 40 else "fail"))
    journey_stages += _render_journey_stage("Respond", result.response_score, "pass" if result.response_score >= 70 else ("warn" if result.response_score >= 40 else "fail"))

    checks_html = "".join(_render_check_row(check) for check in result_dict["checks"])

    evidence_html = ""
    for category in ("detection", "visibility", "diagnosis", "response"):
        if category in result_dict["evidence"] and result_dict["evidence"][category]:
            evidence_html += f"""
            <div style="margin-bottom:20px;">
              <h3 style="margin:0 0 10px;font-size:15px;color:#1f2937;">{category.title()}</h3>
              {_render_evidence_section(category, result_dict['evidence'][category])}
            </div>
            """

    ai_section = ""
    if result.ai_summary:
        ai_section = f"""
        <section style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:16px;margin-bottom:12px;">
          <h2 style="margin:0 0 12px;font-size:18px;">AI Executive Summary</h2>
          <p style="margin:0;line-height:1.6;color:#374151;">{escape(result.ai_summary)}</p>
          {f'<div style="margin-top:12px;padding-top:12px;border-top:1px solid #d1d5db;"><h3 style="margin:0 0 8px;font-size:14px;">AI Recommendations</h3>{_render_recommendations(result.ai_recommendations)}</div>' if result.ai_recommendations else ''}
        </section>
        """

    html = f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Incident Readiness Simulation</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #f6f8fc; color: #1f2937; }}
    .page {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-radius: 12px; padding: 32px; margin-bottom: 24px; }}
    .hero h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .hero .subtitle {{ font-size: 14px; opacity: 0.9; }}
    .hero .incident-badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 6px 12px; border-radius: 6px; font-size: 12px; margin-top: 12px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    .kpi {{ background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; }}
    .kpi .label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }}
    .kpi .value {{ font-size: 32px; font-weight: 700; color: #f43f5e; margin-top: 8px; }}
    .kpi .status {{ font-size: 13px; color: #6b7280; margin-top: 6px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 20px; margin-bottom: 12px; }}
    h2 {{ margin: 0 0 16px; font-size: 20px; color: #1f2937; }}
    .journey {{ display: flex; gap: 20px; justify-content: space-between; margin-bottom: 20px; }}
    .stage {{ flex: 1; text-align: center; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px; text-align: left; }}
    th {{ background: #f9fafb; font-weight: 600; border-bottom: 1px solid #e5e7eb; }}
    .gap-list {{ list-style: none; margin: 0; padding: 0; }}
    .gap-list li {{ padding: 10px; background: #fef2f2; border-left: 3px solid #991b1b; margin-bottom: 8px; border-radius: 4px; }}
    @media (max-width: 768px) {{
      .page {{ padding: 12px; }}
      .hero {{ padding: 20px; }}
      .journey {{ flex-direction: column; gap: 10px; }}
      .kpis {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class='page'>
    <div class='hero'>
      <h1>Incident Readiness Simulation</h1>
      <p class='subtitle'>{escape(result.application_name)} • {escape(result.environment)} • {escape(result.service_name)}</p>
      <div class='incident-badge'>Incident Type: {escape(result.incident_type.replace("_", " ").title())}</div>
    </div>

    <div class='kpis'>
      <div class='kpi'>
        <div class='label'>Overall Readiness</div>
        <div class='value'>{result.overall_readiness_score:.0f}</div>
        <div class='status'>{result.readiness_status}</div>
      </div>
      <div class='kpi'>
        <div class='label'>Detection Score</div>
        <div class='value' style='color:#667eea;'>{result.detection_score:.0f}</div>
      </div>
      <div class='kpi'>
        <div class='label'>Visibility Score</div>
        <div class='value' style='color:#764ba2;'>{result.visibility_score:.0f}</div>
      </div>
      <div class='kpi'>
        <div class='label'>Diagnosis Score</div>
        <div class='value' style='color:#f59e0b;'>{result.diagnosis_score:.0f}</div>
      </div>
      <div class='kpi'>
        <div class='label'>Response Score</div>
        <div class='value' style='color:#10b981;'>{result.response_score:.0f}</div>
      </div>
    </div>

    <div class='card'>
      <h2>Incident Journey Timeline</h2>
      <div class='journey'>
        {journey_stages}
      </div>
    </div>

    <div class='card'>
      <h2>Readiness Checks</h2>
      <table>
        <thead>
          <tr>
            <th style='width:30px;'></th>
            <th>Check</th>
            <th style='width:60px;'>Score</th>
            <th style='width:100px;'>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {checks_html}
        </tbody>
      </table>
    </div>

    {f'<div class="card"><h2>Evidence Map</h2>{evidence_html}</div>' if any(result_dict['evidence'].values()) else ''}

    <div class='card'>
      <h2>Critical Gaps</h2>
      {f'<ul class="gap-list">{"".join(f"<li>{escape(gap)}</li>" for gap in result.gaps)}</ul>' if result.gaps else '<p style="color:#6b7280;">No critical gaps found.</p>'}
    </div>

    <div class='card'>
      <h2>Recommended Actions</h2>
      {_render_recommendations(result.recommendations)}
    </div>

    {ai_section}

    {f'<div class="card"><h2>Extraction Notes</h2><ul style="margin:0;padding-left:20px;">{"".join(f"<li>{escape(err)}</li>" for err in result.extraction_errors)}</ul></div>' if result.extraction_errors else ''}
  </div>
</body>
</html>
"""

    output_path.write_text(html, encoding="utf-8")
    logger.info("Generated incident simulation report at %s", output_path)
    return output_path


def write_incident_simulation_outputs(
    result: IncidentSimulationResult,
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = generate_incident_report(result=result, output_dir=output_dir)

    json_path = output_dir / "incident-simulation.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    logger.info("Generated incident simulation JSON at %s", json_path)

    return {"html": html_path, "json": json_path}
