# ObservaScore

**Observability & SRE Maturity Assessment Tool for Financial Institutions**

ObservaScore connects read-only to your observability stack, extracts configuration and sample telemetry, runs a catalogue of maturity heuristics, and produces an HTML + JSON maturity report with a heatmap, findings, and a prioritized improvement backlog.

This repository is a **working reference implementation** designed for demo use. Point it at your tools via a config file, run one command, get a report.

## Supported Sources (v0.1)

| Tool | Mode | What's extracted |
|---|---|---|
| Prometheus | HTTP API | Targets, rules (alerting + recording), sample series, label cardinality |
| Grafana | HTTP API | Folders, dashboards (full JSON), datasources, alert rules |
| Loki | HTTP API | Labels, label cardinality, log volume sample |
| Jaeger | HTTP API | Services, operations, sample traces |

All adapters are **read-only**. ObservaScore never writes to your tools.

## Quick Start

### 1. Prerequisites

- Python 3.10+
- Network access from wherever you run this to your observability tools
- Prometheus, Grafana, Loki, Jaeger already installed (you have this)

### 2. Clone and install

```bash
git clone https://github.com/<your-username>/observascore.git
cd observascore
python -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -e .
```

### 3. Configure

Copy the example config and edit the URLs:

```bash
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml`:

```yaml
sources:
  prometheus:
    enabled: true
    url: http://YOUR-VM-IP:9090
  grafana:
    enabled: true
    url: http://YOUR-VM-IP:3000
    api_key: "YOUR_GRAFANA_API_KEY"   # Service account token, Viewer role
  loki:
    enabled: true
    url: http://YOUR-VM-IP:3100
  jaeger:
    enabled: true
    url: http://YOUR-VM-IP:16686
```

### 4. Run the assessment

```bash
observascore assess --config config/config.yaml --output ./reports
```

Or without installing:

```bash
python -m observascore.cli assess --config config/config.yaml --output ./reports
```

### 5. View the report

Open `reports/observascore-report.html` in your browser. You'll see:

- Executive summary with overall maturity level
- 7-dimension heatmap
- Findings grouped by severity
- Improvement backlog prioritized
- Technical annex with evidence

A `reports/observascore-report.json` is also produced for programmatic use.

## Offline Demo (no VM needed)

To see what the output looks like before pointing at real tools:

```bash
python examples/demo_offline.py
```

This generates a report from synthetic data showcasing typical FI findings
(cause-heavy alerts, missing runbooks, no SLO recording rules, flat folder
structure, etc). Open `reports/observascore-report.html` to inspect.

## Creating a Grafana API Key

In Grafana:
1. Administration → Service accounts → Add service account
2. Name: `observascore`, Role: `Viewer`
3. Add service account token → copy the token into `config.yaml`

## Architecture

```
  Config ──> Adapters ──> Common Observability Model (COM) ──> Rules Engine ──> Report
              │                                                     │
              ├── Prometheus                                         ├── Signal Coverage
              ├── Grafana                                            ├── Golden Signals
              ├── Loki                                               ├── SLO Maturity
              └── Jaeger                                             ├── Alert Quality
                                                                     ├── Incident Response
                                                                     ├── Automation
                                                                     └── Governance
```

## Adding Your Own Rules

Rules live in `observascore/rules/packs/` as YAML files. See `core-pack.yaml` for examples. Add a file, rerun the assessment — no code changes needed.

## Project Structure

```
observascore/
├── observascore/
│   ├── adapters/        # Tool-specific read-only clients
│   ├── model/           # Common Observability Model dataclasses
│   ├── rules/           # Rules engine + YAML rule packs
│   ├── engine/          # Scoring engine
│   ├── report/          # HTML/JSON report generator + Jinja2 templates
│   └── cli.py           # Command-line interface
├── config/
│   └── config.example.yaml
├── tests/
├── examples/
└── pyproject.toml
```

## License

MIT — use it, fork it, rebrand it for client engagements.

## Disclaimer

This is a reference implementation. Rule weights and thresholds reflect general best practices and should be tuned for your environment. Always validate findings with domain experts before presenting to clients.
