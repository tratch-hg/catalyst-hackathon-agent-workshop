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

---

## Step 1 — Build an agent from scratch (`workshop.py`)

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

Each step builds on the previous one, ending with a fully instrumented agent. Start here.

### How the Claude Agent SDK powers the agentic loop

Every agent in this workshop is built on a single primitive from the Anthropic Python SDK: `client.messages.create()`. That one call is stateless — it takes a list of messages and returns a response. The *agent loop* is just you calling it in a `while True`, accumulating the conversation, until the model signals it's done.

```
messages = [{"role": "user", "content": user_input}]

while True:
    response = client.messages.create(model=..., tools=..., messages=messages)
    messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason == "end_turn":
        break                              # model is done

    elif response.stop_reason == "tool_use":
        # execute your tools, collect results
        messages.append({"role": "user", "content": tool_results})

    elif response.stop_reason == "pause_turn":
        # server-side tool (e.g. web_search) finished — just continue
        messages.append({"role": "user", "content": []})
```

The key thing to notice: **the loop logic lives entirely in your code, not inside any framework**. The SDK just surfaces `stop_reason` so you know what to do next.

### What the Claude Agent SDK gives you for free

- **`stop_reason` replaces text parsing.** No prompt-engineering a stop phrase or regexing the output — the API tells you exactly why the model stopped.
- **Typed content blocks.** Tool calls arrive as structured `ToolUseBlock` objects, not unstructured text you have to parse.
- **Declarative tool schemas.** Define a tool as JSON once; the model knows the exact signature and the API validates the call.
- **Built-in server-side tools.** Web search runs on Anthropic's infrastructure — no external API keys or rate-limit handling on your side.
- **State is just a list.** No sessions, no databases, no serialisation. The conversation history is a plain Python list you append to and pass in each turn.

---

## Step 2 — A portable ReAct agent (`react_agent.py`)

### How the ReAct loop works

ReAct (**Re**asoning + **Act**ing) is a prompting pattern introduced by Yao et al. (2022) that interleaves the model's chain-of-thought reasoning with concrete actions (tool calls). Before taking any action, the model writes out its reasoning as a *Thought*; it then emits an *Action* (a tool call); the environment returns an *Observation* (the tool result); and the cycle repeats until the model produces a final answer.

```
Thought  →  Action  →  Observation
   ↑                        |
   └────────────────────────┘
         (repeat until done)
```

This matters because it makes the agent's behaviour inspectable and steerable — you can read the thought trace to understand *why* the agent called a tool, not just *what* it called. It also tends to reduce hallucination compared to asking the model to answer in one shot, because each action grounds the next reasoning step in real retrieved information.

> **Further reading:** [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629) — Yao et al., 2022 (the original paper). For a broader overview of agent architectures see Anthropic's [Building effective agents](https://www.anthropic.com/research/building-effective-agents) guide.

### `react_agent.py`

If you want more control over the agent loop and want to prevent vendor lock-in, here's how to build a simple ReAct loop that supports multiple LLM providers — Anthropic, OpenAI, DeepSeek, Groq, and local Ollama. Switch providers by changing `PROVIDER` at the top of the file.

**Teaches:** How to abstract agent logic away from provider-specific APIs so the same agentic loop works across different models. Includes a calculator tool and a web search stub.

```bash
python react_agent.py "What is 12 * 34, and who founded HubSpot?"
# or via the run script:
./run_simple-agent.sh "What is 12 * 34, and who founded HubSpot?"
```

---

## Step 3 — Observability with Datadog

Once your agent is running, you'll want visibility into what it's actually doing — which tools it called, how many tokens it used, and where it spent time. The run scripts (`run_competitor-intelligence-agent.sh` and `run_simple-agent.sh`) automatically enable Datadog tracing when `DD_API_KEY` is present in your environment. No code changes required.

**Setup:**

1. Add your Datadog credentials to `.env`:
   ```
   DD_API_KEY=your-datadog-api-key
   DD_SITE=us5.datadoghq.com
   ```

2. Run an agent via its run script:
   ```bash
   ./run_simple-agent.sh "What is 12 * 34, and who founded HubSpot?"
   ```

3. View traces in the [Datadog LLM Observability dashboard](https://us5.datadoghq.com/llm/traces).

Each LLM call, tool call, and agent run is captured as a nested span with input/output content and token counts. Running without `DD_API_KEY` set falls back to plain `python3` with no observability overhead.

---

## Step 4 — A real-world agent (`competitor-intelligence-agent.py`)

Now put it all together. `competitor-intelligence-agent.py` is a complete autonomous agent that researches a company and writes a self-contained HTML intelligence report. It defines three custom tools — `web_search`, `fetch_page`, and `save_report` — and runs an agentic loop until the report is written to disk.

**Teaches:** Tool definition, multi-turn loops, web scraping, structured HTML output, and optional Datadog instrumentation without modifying core agent logic.

```bash
python competitor-intelligence-agent.py "HubSpot"
# or via the run script (enables Datadog automatically if DD_API_KEY is set):
./run_competitor-intelligence-agent.sh "HubSpot"
```

---

## Expected outputs

- **`workshop.py`** — prints step output to stdout as you work through each step.
- **Simple agent** — prints the final answer to stdout.
- **Competitor intelligence agent** — saves an HTML file (e.g. `hubspot_intelligence.html`) to the working directory. Open it in a browser to see the formatted report.
- **Datadog** — traces appear in the LLM Observability dashboard within a few seconds of the agent finishing.
