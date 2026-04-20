# RCA Agent API Reference

## RCAAgent.analyze_incident()

Analyzes a triggered incident and returns a structured RCA report.

**Parameters:**
- `incident_query` (Dict): 
  - `service`: Service experiencing the issue
  - `time_window`: Minutes to analyze (default: 5)
  - `timestamp`: Optional explicit incident timestamp

**Returns:**
- (str): Formatted RCA report

**Example:**
```python
report = agent.analyze_incident({
    'service': 'PaymentService',
    'time_window': 5
})
```

## RCAAgent.detect_cascade()

Identifies downstream services impacted by a root cause.

**Parameters:**
- `root_cause_service`: Service with identified anomaly
- `operation`: Specific operation with issue

**Returns:**
- (Dict): Cascade chain and affected operations