from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def enrich_with_ai(
    simulation_result: dict,
    ai_config: dict | None,
) -> tuple[str, list[str]]:
    """
    Optionally enrich simulation result with AI-generated narrative and recommendations.

    Returns:
        Tuple of (ai_summary, ai_recommendations)
        If AI is not enabled or fails, returns ("", [])
    """
    if not ai_config or not ai_config.get("enabled"):
        return "", []

    provider = ai_config.get("provider")
    api_key = ai_config.get("api_key")

    if not provider or not api_key:
        logger.warning("AI enrichment requested but credentials missing")
        return "", []

    if provider.lower() == "anthropic":
        return _enrich_with_anthropic(simulation_result, api_key)
    elif provider.lower() == "azure":
        return _enrich_with_azure_openai(simulation_result, ai_config)

    logger.warning("Unknown AI provider: %s", provider)
    return "", []


def _enrich_with_anthropic(simulation_result: dict, api_key: str) -> tuple[str, list[str]]:
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an expert SRE writing an executive summary for a production incident readiness simulation.

Simulation Results:
- Application: {simulation_result['application_name']}
- Environment: {simulation_result['environment']}
- Service: {simulation_result['service_name']}
- Incident Type: {simulation_result['incident_type']}
- Overall Score: {simulation_result['overall_readiness_score']}/100
- Status: {simulation_result['readiness_status']}
- Detection Score: {simulation_result['detection_score']:.0f}/100
- Visibility Score: {simulation_result['visibility_score']:.0f}/100
- Diagnosis Score: {simulation_result['diagnosis_score']:.0f}/100
- Response Score: {simulation_result['response_score']:.0f}/100

Gaps:
{chr(10).join(f"- {gap}" for gap in simulation_result['gaps'][:5])}

Recommendations:
{chr(10).join(f"- {rec}" for rec in simulation_result['recommendations'][:5])}

Please provide:
1. A concise 2-3 sentence executive summary of the incident readiness posture.
2. Business impact if this incident occurred today.
3. Top 3 action items to reduce MTTR.

Format as JSON: {{"summary": "...", "impact": "...", "actions": ["...", "...", "..."]}}"""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        response_text = message.content[0].text
        import json
        try:
            parsed = json.loads(response_text)
            summary = parsed.get("summary", "")
            actions = parsed.get("actions", [])
            return summary, actions
        except json.JSONDecodeError:
            return response_text[:300], []

    except Exception as e:
        logger.warning("AI enrichment with Anthropic failed: %s", e)
        return "", []


def _enrich_with_azure_openai(simulation_result: dict, ai_config: dict) -> tuple[str, list[str]]:
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_key=ai_config.get("api_key"),
            api_version="2023-05-15",
            azure_endpoint=ai_config.get("azure_endpoint"),
        )

        prompt = f"""You are an expert SRE writing an executive summary for a production incident readiness simulation.

Simulation Results:
- Application: {simulation_result['application_name']}
- Environment: {simulation_result['environment']}
- Service: {simulation_result['service_name']}
- Incident Type: {simulation_result['incident_type']}
- Overall Score: {simulation_result['overall_readiness_score']}/100
- Status: {simulation_result['readiness_status']}

Please provide a 2-3 sentence executive summary and top 3 action items."""

        response = client.chat.completions.create(
            engine=ai_config.get("azure_deployment", "gpt-35-turbo"),
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
        )

        return response.choices[0].message.content, []

    except Exception as e:
        logger.warning("AI enrichment with Azure OpenAI failed: %s", e)
        return "", []
