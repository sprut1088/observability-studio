from typing import Dict, Any
from signal_collector import SignalCollector
from correlation_engine import CorrelationEngine
from cascade_detector import CascadeDetector
from llm_formatter import LLMFormatter

class RCAAgent:
    """
    Main RCA Agent orchestrator.
    Coordinates signal collection, correlation, cascade detection, and reporting.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.signal_collector = SignalCollector(config)
        self.correlation_engine = CorrelationEngine(config)
        self.cascade_detector = CascadeDetector(
            self.signal_collector.fetch_service_dependencies('all')
        )
        self.llm_formatter = LLMFormatter()
    
    def analyze_incident(self, incident_query: Dict[str, Any]) -> str:
        """
        Main entry point. Takes incident query and returns RCA report.
        
        Args:
            incident_query: {
                'service': 'PaymentService',
                'time_window': 5,  # minutes
                'alert_name': 'High latency detected'
            }
        
        Returns:
            Formatted RCA report (string)
        """
        
        # Step 1: Collect signals
        print("[RCA] Collecting signals...")
        signals = self._collect_signals(incident_query)
        
        # Step 2: Identify root causes
        print("[RCA] Analyzing correlation...")
        root_causes = self.correlation_engine.identify_root_causes(signals)
        
        if not root_causes:
            return "─ RCA Report ──────────────────────────────\nNo anomalies detected in the time window."
        
        # Step 3: Detect cascade
        print("[RCA] Detecting cascade...")
        primary_rc = root_causes[0]
        cascade = self.cascade_detector.detect_cascade(
            primary_rc.service,
            [primary_rc.operation]
        )
        
        # Step 4: Format via Claude
        print("[RCA] Generating report...")
        analysis = {
            'root_causes': root_causes,
            'cascade': cascade,
            'signals': signals.get('metrics', {}),
            'timestamp': incident_query.get('timestamp')
        }
        
        report = self.llm_formatter.format_rca_report(analysis)
        return report
    
    def _collect_signals(self, incident_query: Dict) -> Dict[str, Any]:
        """Aggregate signals from all sources."""
        service = incident_query.get('service')
        time_window = incident_query.get('time_window', 5)
        
        return {
            'traces': self.signal_collector.fetch_traces(service, None, time_window),
            'metrics': self.signal_collector.fetch_metrics(service, time_window),
            'logs': self.signal_collector.fetch_logs(service, time_window),
            'dependencies': self.signal_collector.fetch_service_dependencies(service)
        }