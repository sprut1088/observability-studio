# tests/test_rca_agent.py
import unittest
from unittest.mock import patch, MagicMock
from src.rca_agent import RCAAgent
from src.correlation_engine import RootCauseCandidate

class TestRCAAgent(unittest.TestCase):
    
    def setUp(self):
        self.config = {
            'splunk_realm': 'us0',
            'log_level': 'DEBUG'
        }
        self.agent = RCAAgent(self.config)
    
    @patch('src.rca_agent.SignalCollector')
    def test_analyze_incident_payment_service(self, mock_collector):
        """Test RCA for payment service latency incident."""
        
        # Mock signal collection
        mock_collector_instance = MagicMock()
        mock_collector.return_value = mock_collector_instance
        
        mock_collector_instance.fetch_metrics.return_value = {
            'PaymentService/Charge': {
                'latency_p99': 600008.56,
                'error_rate': 15.5,
                'throughput': 100
            }
        }
        
        # Test
        incident_query = {
            'service': 'PaymentService',
            'time_window': 5
        }
        
        report = self.agent.analyze_incident(incident_query)
        
        # Assertions
        self.assertIn('RCA Report', report)
        self.assertIn('PaymentService', report)
        self.assertIn('Charge', report)

if __name__ == '__main__':
    unittest.main()