# Observability Studio — Knowledge Agent

A conversational knowledge agent built with [Google ADK v2](https://github.com/google/adk-python) that answers questions about the Observability Studio project.

## What it does

The agent embeds the project README as its system prompt so it can answer questions like:
- "What is Observability Studio?"
- "What modules does it have?"
- "Which observability tools are supported?"
- "What does the Observability Gap Map do?"

It uses **Claude** (via the LiteLLM bridge) as the underlying LLM.

## Setup

### 1. Create a virtual environment (Python 3.11+ recommended)

```bash
python -m venv obs_agent/.venv
# Windows
obs_agent\.venv\Scripts\activate
# macOS/Linux
source obs_agent/.venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r obs_agent/requirements.txt
```

### 3. Configure your API key

```bash
cp obs_agent/.env.example obs_agent/.env
# Edit obs_agent/.env and set ANTHROPIC_API_KEY
```

## Running

From the **repo root**:

```bash
# Interactive CLI session
adk run obs_agent/obs_agent

# Browser-based chat UI
adk web obs_agent/obs_agent
```

## Structure

```
obs_agent/
├── obs_agent/
│   ├── __init__.py   # exports root_agent (ADK discovery)
│   └── agent.py      # LlmAgent definition with embedded project knowledge
├── .env.example      # API key template
├── requirements.txt
└── README.md
```

## Notes

- Claude is accessed through the LiteLLM bridge (`anthropic/claude-sonnet-4-5`). The Python ADK does not support Anthropic Claude natively.
- The agent is read-only / knowledge-only — no tools are attached.
- To update the knowledge base, edit the `PROJECT_KNOWLEDGE` string in `obs_agent/agent.py`.
