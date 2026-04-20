import anthropic
from typing import Dict, Any

class LLMFormatter:
    """
    Uses Claude API to structure signal correlation results into
    human-readable RCA reports with recommendations.
    """
    
    def __init__(self):
        self.client = anthropic.Anthropic()
    
    def format_rca_report(self, analysis: Dict[str, Any]) -> str:
        """
        Input: Dict with root causes, cascade, metrics
        Output: Formatted RCA report (string)
        
        Uses Claude to:
        1. Synthesize findings into narrative
        2. Generate actionable recommendations
        3. Estimate impact scope
        """
        
        prompt = self._build_prompt(analysis)
        
        message = self.client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            system="""You are an expert SRE analyzing observability data.
Generate a concise RCA report in this exact format:

─ RCA Report ──────────────────────────────
Incident: [brief service/operation description]
Root Cause: [identified bottleneck with specific metrics]
Cascade: [downstream services affected]
First seen: [earliest timestamp or 'unknown']
Recommendation: [specific, actionable steps]

Be precise. Use exact metric values from the analysis."""
        )
        
        return message.content[0].text
    
    def _build_prompt(self, analysis: Dict[str, Any]) -> str:
        """Construct prompt from analysis data."""
        root_causes = analysis.get('root_causes', [])
        cascade = analysis.get('cascade', {})
        signals = analysis.get('signals', {})
        
        prompt = f"""Analyze this incident:

Root Cause Candidates:
{self._format_candidates(root_causes)}

Service Cascade:
{self._format_cascade(cascade)}

Signals:
{self._format_signals(signals)}

Generate an RCA report."""
        
        return prompt
    
    def _format_candidates(self, candidates) -> str:
        """Format root cause candidates."""
        lines = []
        for rc in candidates[:3]:  # Top 3
            lines.append(f"- {rc.service}/{rc.operation}: {rc.metric_value}ms "
                        f"(threshold: {rc.threshold}ms, severity: {rc.severity:.2f})")
        return "\n".join(lines)
    
    def _format_cascade(self, cascade: Dict) -> str:
        """Format cascade chain."""
        chain = cascade.get('cascade_chain', [])
        return " ".join(chain) if chain else "No cascade detected"
    
    def _format_signals(self, signals: Dict) -> str:
        """Format signals summary."""
        lines = []
        for service, metrics in signals.items():
            lines.append(f"- {service}: Error rate {metrics.get('error_rate', 0)}%, "
                        f"Latency p99: {metrics.get('latency_p99', 0)}ms")
        return "\n".join(lines)