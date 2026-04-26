# Catalyst Hackathon — Agent Workshop

A hands-on workshop for building autonomous AI agents with the Claude API. You'll start from a single API call and finish with a production-style agent that searches the web, generates reports, and optionally streams traces to Datadog LLM Observability.

## Prerequisites

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/) (required)
- A [Datadog account](https://app.datadoghq.com/) with an API key (optional — for the observability step)

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment variables
cp .env.example .env
```

Open `.env` and fill in your Anthropic key at minimum:

```
ANTHROPIC_API_KEY=sk-ant-...

# Optional — only needed for the Datadog observability step
# DD_API_KEY=
# DD_SITE=us5.datadoghq.com
```

## Workshop

The main workshop file is `workshop.py`. It walks through six self-contained steps — run any step by changing the `STEP` variable at the bottom of the file, then:

```bash
python workshop.py
```

| Step | What you build | What it teaches |
|------|---------------|-----------------|
| 1 | Hello World | Basic `client.messages.create()` call structure |
| 2 | Define a tool | Tool schemas as JSON; `stop_reason: "tool_use"` |
| 3 | Execute a tool | The request → tool call → result → response cycle |
| 4 | Agent loop | Multi-turn `while True` loop until `"end_turn"` |
| 5 | Full agent | Server-side web search, HTML report generation, `"pause_turn"` |
| 6 | Add observability | Wrapping agent/LLM/tool calls in Datadog spans |

## Agent files

### `workshop.py`
The primary teaching artifact. Each step builds on the previous one, ending with a fully instrumented agent. Start here.

### `competitor-intelligence-agent.py`
A complete autonomous agent that researches a company and writes a self-contained HTML intelligence report. It defines three custom tools — `web_search`, `fetch_page`, and `save_report` — and runs an agentic loop until the report is written to disk.

**Teaches:** Tool definition, multi-turn loops, web scraping, structured HTML output, and optional Datadog instrumentation without modifying core agent logic.

```bash
python competitor-intelligence-agent.py "HubSpot"
# or via the run script (enables Datadog automatically if DD_API_KEY is set):
./run_competitor-intelligence-agent.sh "HubSpot"
```

### `agent.py`
A compact ReAct agent that supports multiple LLM providers — Anthropic, OpenAI, DeepSeek, Groq, and local Ollama — through a unified interface. Switch providers by changing `PROVIDER` at the top of the file.

**Teaches:** How to abstract agent logic away from provider-specific APIs so the same agentic loop works across different models. Includes a calculator tool and a web search stub.

```bash
python agent.py "What is 12 * 34, and who founded HubSpot?"
# or via the run script:
./run.sh "What is 12 * 34, and who founded HubSpot?"
```

## Optional: Datadog LLM Observability

The run scripts (`run_competitor-intelligence-agent.sh` and `simple_agent/run.sh`) automatically enable Datadog tracing when `DD_API_KEY` is present in your environment. No code changes required.

**Setup:**

1. Add your Datadog credentials to `.env`:
   ```
   DD_API_KEY=your-datadog-api-key
   DD_SITE=us5.datadoghq.com
   ```

2. Run an agent via its run script:
   ```bash
   ./run_competitor-intelligence-agent.sh "Salesforce"
   ```

3. View traces in the [Datadog LLM Observability dashboard](https://us5.datadoghq.com/llm/traces).

Each LLM call, tool call, and agent run is captured as a nested span with input/output content and token counts. Running without `DD_API_KEY` set falls back to plain `python3` with no observability overhead.

## Expected outputs

- **Competitor intelligence agent** — saves an HTML file (e.g. `salesforce_intelligence.html`) to the working directory. Open it in a browser to see the formatted report.
- **Simple agent** — prints the final answer to stdout.
- **Datadog** — traces appear in the LLM Observability dashboard within a few seconds of the agent finishing.
