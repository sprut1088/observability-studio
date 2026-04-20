from flask import Flask, request
from rca_agent import RCAAgent

app = Flask(__name__)
agent = RCAAgent(config)

@app.route('/trigger-rca', methods=['POST'])
def trigger_rca():
    """Webhook endpoint for alert systems (Splunk, Datadog, PagerDuty)."""
    incident = request.json
    
    report = agent.analyze_incident({
        'service': incident.get('service'),
        'time_window': incident.get('window', 5),
        'alert_name': incident.get('alert_name')
    })
    
    return {
        'status': 'success',
        'report': report
    }

if __name__ == '__main__':
    app.run(port=5000)