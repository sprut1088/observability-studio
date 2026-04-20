# RCA Agent Accelerator

AI-powered Root Cause Analysis for incident response.

## Features
- **Signal Correlation**: Ingests traces, metrics, and logs
- **Root Cause Identification**: Statistically ranks anomalies
- **Cascade Detection**: Traces errors through service topology
- **LLM-Powered Reports**: Claude API structures findings into actionable reports
- **Multi-source Support**: Splunk O11y, Grafana, Jaeger, Prometheus

## Quick Start

### Installation
```bash
pip install -r requirements.txt
```

### Usage
```python
from src.rca_agent import RCAAgent

agent = RCAAgent({
    'splunk_realm': 'us0',
    'log_level': 'INFO'
})

report = agent.analyze_incident({
    'service': 'PaymentService',
    'time_window': 5
})

print(report)
```

### Configuration
Edit `config/integrations.yaml` to connect your observability sources.

## Architecture
- `signal_collector.py`: Fetch observability data
- `correlation_engine.py`: Identify root causes
- `cascade_detector.py`: Trace error propagation
- `llm_formatter.py`: Claude API integration for report generation

## Output Example
```
─ RCA Report ──────────────────────────────
Incident: Payment service is experiencing errors and latency issues
Root Cause: oteldemo.PaymentService/Charge operation taking 600008.56ms avg
Cascade: Error cascading to oteldemo.CheckoutService/PlaceOrder
First seen: 2026-04-19T12:00:00Z
Recommendation: Investigate PaymentService/Charge latency causes and optimize...



## Accelerators

### RCA Agent
**Purpose:** Automated root cause analysis for incident response  
**Status:** GA  
**Input:** Service alerts, incident queries  
**Output:** Structured RCA reports with cascade detection and recommendations  

**Features:**
- Signal correlation (traces, metrics, logs)
- Statistical root cause ranking
- Service dependency cascade tracing
- Claude API-powered narrative generation
- Multi-source observability support (Splunk, Grafana, Prometheus)

**Path:** `accelerators/rca-agent/`  
**Setup:** `accelerators/rca-agent/docs/SETUP.md`