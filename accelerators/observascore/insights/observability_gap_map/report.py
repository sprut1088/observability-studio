from __future__ import annotations

import json
import logging
from pathlib import Path

from observascore.insights.observability_gap_map.models import ObservabilityGapMapResult

logger = logging.getLogger(__name__)


def _build_payload(result: ObservabilityGapMapResult) -> dict:
    payload = result.to_dict()
    payload["services"] = payload.pop("services", [])
    return payload


def generate_observability_gap_map_report(
    result: ObservabilityGapMapResult,
    output_dir: Path,
    filename: str = "observability-gap-map-report.html",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / filename

    payload = _build_payload(result)
    payload_json = json.dumps(payload)

    no_services_message = ""
    if result.no_services_inferred:
        no_services_message = (
            "<div class='banner banner-warning'>No services could be confidently inferred from the selected tools.</div>"
        )

    no_dashboards_message = ""
    if result.no_dashboards_found:
        no_dashboards_message = (
            "<div class='banner banner-info'>No dashboards were discovered. Dashboard and RED coverage are shown as missing.</div>"
        )

    extraction_errors_html = ""
    if result.extraction_errors:
        extraction_errors_html = "<section class='panel'><h2>Extraction Errors</h2><ul class='error-list'>"
        extraction_errors_html += "".join(f"<li>{err}</li>" for err in result.extraction_errors)
        extraction_errors_html += "</ul></section>"

    html = f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Observability Gap Map</title>
  <style>
    :root {{
      --bg: #f2f5fb;
      --panel: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --good: #10b981;
      --warn: #f59e0b;
      --bad: #ef4444;
      --chip-bg: #f3f4f6;
      --hero: linear-gradient(125deg, #1d4ed8, #0ea5e9 45%, #14b8a6 100%);
      --shadow: 0 12px 30px rgba(17, 24, 39, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; color: var(--text); background: var(--bg); }}
    .page {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    .hero {{ background: var(--hero); color: #fff; border-radius: 20px; padding: 28px; box-shadow: var(--shadow); }}
    .hero h1 {{ margin: 0 0 8px; font-size: 34px; }}
    .hero p {{ margin: 0; opacity: 0.92; }}
    .hero-grid {{ margin-top: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
    .hero-metric {{ background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.18); border-radius: 14px; padding: 12px; }}
    .hero-metric .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .9; }}
    .hero-metric .value {{ margin-top: 6px; font-size: 28px; font-weight: 700; }}

    .banner {{ margin-top: 16px; border-radius: 10px; padding: 10px 12px; font-size: 14px; }}
    .banner-warning {{ background: #fff7ed; border: 1px solid #fdba74; color: #9a3412; }}
    .banner-info {{ background: #eff6ff; border: 1px solid #93c5fd; color: #1e3a8a; }}

    .layout {{ margin-top: 20px; display: grid; gap: 16px; grid-template-columns: 2fr 1fr; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow); padding: 16px; }}
    .panel h2 {{ margin: 0 0 12px; font-size: 18px; }}

    .filters {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-bottom: 12px; }}
    .filters input, .filters select {{ width: 100%; border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px; font: inherit; }}

    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #edf0f5; padding: 8px; text-align: left; vertical-align: middle; }}
    th {{ background: #f8fafc; position: sticky; top: 0; z-index: 1; }}
    .matrix-wrap {{ max-height: 520px; overflow: auto; border: 1px solid var(--line); border-radius: 12px; }}

    .chip {{ display: inline-flex; align-items: center; justify-content: center; min-width: 54px; border-radius: 999px; padding: 2px 8px; font-size: 12px; border: 1px solid transparent; }}
    .chip-present {{ background: #dcfce7; color: #166534; border-color: #86efac; }}
    .chip-partial {{ background: #fef3c7; color: #92400e; border-color: #fcd34d; }}
    .chip-missing {{ background: #fee2e2; color: #991b1b; border-color: #fca5a5; }}

    .status {{ font-weight: 600; }}
    .status-Excellent {{ color: #047857; }}
    .status-Good {{ color: #0f766e; }}
    .status-Partial {{ color: #b45309; }}
    .status-Poor {{ color: #c2410c; }}
    .status-Blind-Spot {{ color: #b91c1c; }}

    .radar-list {{ display: flex; flex-direction: column; gap: 10px; }}
    .radar-item {{ border: 1px solid var(--line); border-radius: 10px; padding: 10px; background: #fafcff; }}
    .radar-head {{ display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }}
    .bar {{ margin-top: 8px; height: 8px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
    .bar > span {{ display: block; height: 100%; background: linear-gradient(90deg, #ef4444, #f59e0b, #22c55e); }}

    .heatmap {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }}
    .heat-card {{ border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: #fafcff; }}
    .heat-label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }}
    .heat-value {{ font-size: 22px; font-weight: 700; margin: 4px 0; }}

    .tool-grid {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 10px; }}
    .tool-card {{ border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: #fafcff; }}
    .tool-name {{ font-weight: 700; margin-bottom: 8px; }}
    .tool-meta {{ font-size: 12px; color: var(--muted); line-height: 1.6; }}

    .rec-grid {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
    .rec-col {{ border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: #fafcff; }}
    .rec-col h3 {{ margin: 0 0 8px; font-size: 14px; }}
    .rec-item {{ border-top: 1px dashed #e5e7eb; padding-top: 8px; margin-top: 8px; font-size: 12px; }}
    .rec-item:first-child {{ border-top: 0; margin-top: 0; padding-top: 0; }}
    .rec-service {{ font-weight: 700; }}

    .topology {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .service-node {{ border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 10px; }}
    .node-title {{ font-weight: 700; font-size: 13px; margin-bottom: 8px; }}
    .node-chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .node-chip {{ font-size: 11px; background: #eef2ff; color: #3730a3; border-radius: 999px; padding: 3px 8px; }}

    .error-list {{ margin: 0; padding-left: 18px; color: #991b1b; font-size: 13px; }}

    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class='page'>
    <header class='hero'>
      <h1>Observability Gap Map</h1>
      <p>Interactive service-level blind spot analysis across metrics, logs, traces, dashboards, alerts, and RED coverage.</p>
      <div class='hero-grid'>
        <div class='hero-metric'><div class='label'>Overall Coverage</div><div class='value' id='heroOverall'>0</div></div>
        <div class='hero-metric'><div class='label'>Service Count</div><div class='value' id='heroServices'>0</div></div>
        <div class='hero-metric'><div class='label'>Blind Spots</div><div class='value' id='heroBlind'>0</div></div>
        <div class='hero-metric'><div class='label'>Highest Risk Service</div><div class='value' id='heroRisk' style='font-size:20px'>n/a</div></div>
      </div>
      {no_services_message}
      {no_dashboards_message}
    </header>

    <div class='layout'>
      <section class='panel'>
        <h2>Interactive Service Coverage Matrix</h2>
        <div class='filters'>
          <input id='filterSearch' placeholder='Search service' />
          <select id='filterStatus'>
            <option value='all'>All statuses</option>
            <option value='Excellent'>Excellent</option>
            <option value='Good'>Good</option>
            <option value='Partial'>Partial</option>
            <option value='Poor'>Poor</option>
            <option value='Blind Spot'>Blind Spot</option>
          </select>
          <select id='filterMissing'>
            <option value='all'>Any missing signal</option>
            <option value='metrics'>Missing metrics</option>
            <option value='logs'>Missing logs</option>
            <option value='traces'>Missing traces</option>
            <option value='dashboards'>Missing dashboards</option>
            <option value='alerts'>Missing alerts</option>
            <option value='red'>Missing RED</option>
          </select>
          <select id='filterTool'>
            <option value='all'>Any tool</option>
          </select>
        </div>
        <div class='matrix-wrap'>
          <table>
            <thead>
              <tr>
                <th>Service</th>
                <th>Status</th>
                <th>Score</th>
                <th>Metrics</th>
                <th>Logs</th>
                <th>Traces</th>
                <th>Dashboards</th>
                <th>Alerts</th>
                <th>Rate</th>
                <th>Errors</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody id='matrixRows'></tbody>
          </table>
        </div>
      </section>

      <section class='panel'>
        <h2>Blind Spot Radar</h2>
        <div id='radarList' class='radar-list'></div>
      </section>
    </div>

    <section class='panel'>
      <h2>Signal Heatmap</h2>
      <div id='heatmap' class='heatmap'></div>
    </section>

    <section class='panel'>
      <h2>Tool Coverage Summary</h2>
      <div id='toolGrid' class='tool-grid'></div>
      <div class='matrix-wrap'>
        <table>
          <thead>
            <tr>
              <th>Tool</th>
              <th>Total Services</th>
              <th>Metrics</th>
              <th>Logs</th>
              <th>Traces</th>
              <th>Dashboards</th>
              <th>Alerts</th>
              <th>RED Complete</th>
            </tr>
          </thead>
          <tbody id='toolRows'></tbody>
        </table>
      </div>
    </section>

    <section class='panel'>
      <h2>Recommendations Board</h2>
      <div class='rec-grid'>
        <div class='rec-col'><h3>Critical blind spots</h3><div id='recCritical'></div></div>
        <div class='rec-col'><h3>High-value quick wins</h3><div id='recHigh'></div></div>
        <div class='rec-col'><h3>Dashboard/RED improvements</h3><div id='recDashboard'></div></div>
      </div>
    </section>

    <section class='panel'>
      <h2>Mini Topology</h2>
      <div id='topology' class='topology'></div>
    </section>

    {extraction_errors_html}
  </div>

  <script>
    const data = {payload_json};

    const state = {{
      search: '',
      status: 'all',
      missing: 'all',
      tool: 'all',
    }};

    const byId = (id) => document.getElementById(id);

    function chip(value, uncertain) {{
      let css = 'chip-missing';
      let text = '❌ Missing';
      if (value) {{
        if (uncertain) {{
          css = 'chip-partial';
          text = '⚠️ Partial';
        }} else {{
          css = 'chip-present';
          text = '✅ Present';
        }}
      }}
      return `<span class='chip ${{css}}'>${{text}}</span>`;
    }}

    function renderHero() {{
      byId('heroOverall').textContent = (data.overall_coverage_score ?? 0).toFixed(1);
      byId('heroServices').textContent = data.total_services || 0;
      byId('heroBlind').textContent = data.blind_spot_services || 0;
      byId('heroRisk').textContent = (data.weakest_services && data.weakest_services[0]) || 'n/a';
    }}

    function filteredServices() {{
      return (data.services || []).filter((service) => {{
        if (state.search && !service.service.includes(state.search.toLowerCase())) return false;
        if (state.status !== 'all' && service.readiness_status !== state.status) return false;
        if (state.missing !== 'all' && !service.missing_signals.includes(state.missing)) return false;
        if (state.tool !== 'all' && !(service.tools || []).includes(state.tool)) return false;
        return true;
      }});
    }}

    function renderMatrix() {{
      const rows = filteredServices();
      byId('matrixRows').innerHTML = rows.map((service) => {{
        const c = service.coverage || {{}};
        const uncertain = service.service === 'unknown' || service.service === 'platform';
        return `
          <tr>
            <td><strong>${{service.service}}</strong><div style='color:#6b7280;font-size:11px;'>${{(service.tools || []).join(', ') || 'n/a'}}</div></td>
            <td><span class='status status-${{service.readiness_status.replace(/\\s+/g, '-')}}'>${{service.readiness_status}}</span></td>
            <td>${{service.coverage_score}}</td>
            <td>${{chip(c.metrics_present, uncertain && c.metrics_present)}}</td>
            <td>${{chip(c.logs_present, uncertain && c.logs_present)}}</td>
            <td>${{chip(c.traces_present, uncertain && c.traces_present)}}</td>
            <td>${{chip(c.dashboards_present, uncertain && c.dashboards_present)}}</td>
            <td>${{chip(c.alerts_present, uncertain && c.alerts_present)}}</td>
            <td>${{chip(c.rate_present, false)}}</td>
            <td>${{chip(c.errors_present, false)}}</td>
            <td>${{chip(c.duration_present, false)}}</td>
          </tr>
        `;
      }}).join('') || `<tr><td colspan='11' style='text-align:center;color:#6b7280;'>No services match current filters.</td></tr>`;
    }}

    function renderRadar() {{
      const risky = [...(data.services || [])]
        .sort((a, b) => a.coverage_score - b.coverage_score)
        .slice(0, 8);

      byId('radarList').innerHTML = risky.map((service) => {{
        const majorMissing = ['metrics', 'logs', 'traces', 'dashboards', 'alerts']
          .filter((s) => service.missing_signals.includes(s)).length;
        return `
          <div class='radar-item'>
            <div class='radar-head'>
              <strong>${{service.service}}</strong>
              <span>${{service.coverage_score}} / 100</span>
            </div>
            <div style='font-size:12px;color:#6b7280;'>Missing major signals: ${{majorMissing}}</div>
            <div class='bar'><span style='width:${{service.coverage_score}}%'></span></div>
          </div>
        `;
      }}).join('') || `<div style='color:#6b7280;'>No services available.</div>`;
    }}

    function pct(field) {{
      const services = data.services || [];
      if (!services.length) return 0;
      const count = services.filter((s) => s.coverage && s.coverage[field]).length;
      return Math.round((count / services.length) * 100);
    }}

    function pctRedComplete() {{
      const services = data.services || [];
      if (!services.length) return 0;
      const count = services.filter((s) => {{
        const c = s.coverage || {{}};
        return c.rate_present && c.errors_present && c.duration_present;
      }}).length;
      return Math.round((count / services.length) * 100);
    }}

    function renderHeatmap() {{
      const items = [
        ['Metrics', pct('metrics_present')],
        ['Logs', pct('logs_present')],
        ['Traces', pct('traces_present')],
        ['Dashboards', pct('dashboards_present')],
        ['Alerts', pct('alerts_present')],
        ['RED complete', pctRedComplete()],
      ];
      byId('heatmap').innerHTML = items.map(([label, value]) => `
        <div class='heat-card'>
          <div class='heat-label'>${{label}}</div>
          <div class='heat-value'>${{value}}%</div>
          <div class='bar'><span style='width:${{value}}%'></span></div>
        </div>
      `).join('');
    }}

    function renderToolSummary() {{
      const tools = data.tool_coverage_summary || [];
      byId('toolGrid').innerHTML = tools.map((tool) => `
        <div class='tool-card'>
          <div class='tool-name'>${{tool.tool_name}}</div>
          <div class='tool-meta'>
            Metrics: ${{tool.metrics_services}}<br/>
            Logs: ${{tool.logs_services}}<br/>
            Traces: ${{tool.traces_services}}<br/>
            Dashboards: ${{tool.dashboards_services}}<br/>
            Alerts: ${{tool.alerts_services}}<br/>
            RED complete: ${{tool.red_complete_services}}
          </div>
        </div>
      `).join('') || `<div style='color:#6b7280;'>No tool coverage data.</div>`;

      byId('toolRows').innerHTML = tools.map((tool) => `
        <tr>
          <td>${{tool.tool_name}}</td>
          <td>${{tool.total_services}}</td>
          <td>${{tool.metrics_services}}</td>
          <td>${{tool.logs_services}}</td>
          <td>${{tool.traces_services}}</td>
          <td>${{tool.dashboards_services}}</td>
          <td>${{tool.alerts_services}}</td>
          <td>${{tool.red_complete_services}}</td>
        </tr>
      `).join('') || `<tr><td colspan='8' style='text-align:center;color:#6b7280;'>No tools found.</td></tr>`;

      const toolSelect = byId('filterTool');
      const current = toolSelect.value;
      toolSelect.innerHTML = `<option value='all'>Any tool</option>` + tools.map((t) => `<option value='${{t.tool_name}}'>${{t.tool_name}}</option>`).join('');
      if ([...toolSelect.options].some((o) => o.value === current)) toolSelect.value = current;
    }}

    function renderRecommendations() {{
      const recs = data.top_recommendations || [];
      const critical = recs.filter((r) => r.impact === 'critical').slice(0, 10);
      const high = recs.filter((r) => r.impact === 'high').slice(0, 10);
      const dashboard = recs.filter((r) => r.impact === 'dashboard').slice(0, 10);

      function block(list) {{
        return list.map((r) => `
          <div class='rec-item'>
            <div class='rec-service'>${{r.service}} · missing ${{r.missing_signal}}</div>
            <div>${{r.action}}</div>
            <div style='color:#6b7280;margin-top:4px;'>Expected value: ${{r.expected_value}}</div>
          </div>
        `).join('') || `<div style='color:#6b7280;font-size:12px;'>No recommendations in this group.</div>`;
      }}

      byId('recCritical').innerHTML = block(critical);
      byId('recHigh').innerHTML = block(high);
      byId('recDashboard').innerHTML = block(dashboard);
    }}

    function renderTopology() {{
      const services = [...(data.services || [])].sort((a, b) => b.coverage_score - a.coverage_score).slice(0, 10);
      byId('topology').innerHTML = services.map((service) => {{
        const chips = [];
        const c = service.coverage || {{}};
        if (c.metrics_present) chips.push('metrics');
        if (c.logs_present) chips.push('logs');
        if (c.traces_present) chips.push('traces');
        if (c.dashboards_present) chips.push('dashboards');
        if (c.alerts_present) chips.push('alerts');
        if (c.rate_present && c.errors_present && c.duration_present) chips.push('RED');

        return `
          <div class='service-node'>
            <div class='node-title'>${{service.service}} <span style='color:#6b7280;font-weight:500;'>(${{service.coverage_score}})</span></div>
            <div class='node-chips'>${{chips.map((chip) => `<span class='node-chip'>${{chip}}</span>`).join('') || `<span class='node-chip' style='background:#fee2e2;color:#991b1b;'>blind spot</span>`}}</div>
          </div>
        `;
      }}).join('') || `<div style='color:#6b7280;'>No services available.</div>`;
    }}

    function bindFilters() {{
      byId('filterSearch').addEventListener('input', (event) => {{
        state.search = event.target.value.trim().toLowerCase();
        renderMatrix();
      }});
      byId('filterStatus').addEventListener('change', (event) => {{
        state.status = event.target.value;
        renderMatrix();
      }});
      byId('filterMissing').addEventListener('change', (event) => {{
        state.missing = event.target.value;
        renderMatrix();
      }});
      byId('filterTool').addEventListener('change', (event) => {{
        state.tool = event.target.value;
        renderMatrix();
      }});
    }}

    function init() {{
      renderHero();
      renderToolSummary();
      renderMatrix();
      renderRadar();
      renderHeatmap();
      renderRecommendations();
      renderTopology();
      bindFilters();
    }}

    init();
  </script>
</body>
</html>
"""

    html_path.write_text(html, encoding="utf-8")
    logger.info("Generated observability gap map HTML report at %s", html_path)
    return html_path


def write_observability_gap_map_outputs(result: ObservabilityGapMapResult, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = generate_observability_gap_map_report(result=result, output_dir=output_dir)

    json_path = output_dir / "observability-gap-map.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    logger.info("Generated observability gap map JSON report at %s", json_path)
    return {"html": html_path, "json": json_path}
