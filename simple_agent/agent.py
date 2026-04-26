"""
Simple ReAct Agent — Multi-Provider
-------------------------------------
Supports Anthropic, OpenAI, DeepSeek, Groq, and Ollama out of the box.
Change PROVIDER at the top to switch.

Observability via Datadog LLM Observability (optional).
Set DD_API_KEY + DD_SITE env vars and install ddtrace to enable.

Run:  ./run.sh "What is 12 * 34, and who founded HubSpot?"
  or:  python3 agent.py "What is 12 * 34, and who founded HubSpot?"
"""

import json
import os
import sys
import requests

# ---------------------------------------------------------------------------
# Datadog LLM Observability (optional — works without it if not configured)
# ---------------------------------------------------------------------------
try:
    from ddtrace.llmobs import LLMObs
    _DD_AVAILABLE = True
except ImportError:
    _DD_AVAILABLE = False

DD_ENABLED = _DD_AVAILABLE and bool(os.environ.get("DD_API_KEY"))
if DD_ENABLED:
    LLMObs.enable(ml_app="simple-agent", agentless_enabled=True)

# ---------------------------------------------------------------------------
# Pick your provider  →  "anthropic" | "openai" | "deepseek" | "groq" | "ollama"
# ---------------------------------------------------------------------------
PROVIDER = "anthropic"

CONFIGS = {
    "anthropic": {
        "url":     "https://api.anthropic.com/v1/messages",
        "key_env": "ANTHROPIC_API_KEY",
        "model":   "claude-sonnet-4-6",
        "format":  "anthropic",
    },
    "openai": {
        "url":     "https://api.openai.com/v1/chat/completions",
        "key_env": "OPENAI_API_KEY",
        "model":   "gpt-4o",
        "format":  "openai",
    },
    "deepseek": {
        "url":     "https://api.deepseek.com/v1/chat/completions",
        "key_env": "DEEPSEEK_API_KEY",
        "model":   "deepseek-chat",
        "format":  "openai",   # OpenAI-compatible
    },
    "groq": {
        "url":     "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "model":   "llama-3.3-70b-versatile",
        "format":  "openai",   # OpenAI-compatible
    },
    "ollama": {
        "url":     "http://localhost:11434/v1/chat/completions",
        "key_env": None,       # No API key needed — runs locally
        "model":   "llama3.2",
        "format":  "openai",   # OpenAI-compatible
    },
}

cfg = CONFIGS[PROVIDER]
API_KEY = os.environ.get(cfg["key_env"], "") if cfg["key_env"] else ""

# ---------------------------------------------------------------------------
# Tool definitions — written once in Anthropic format, auto-converted below
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "search",
        "description": "Search the web for information on any topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "calculator",
        "description": "Evaluate a maths expression and return the result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "e.g. '12 * 34'"}
            },
            "required": ["expression"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
def search(query: str) -> str:
    """Stub — swap in a real search API here."""
    return (
        f"[search stub for '{query}'] "
        "HubSpot was founded in 2006 by Brian Halligan and Dharmesh Shah at MIT."
    )

def calculator(expression: str) -> str:
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as exc:
        return f"Error: {exc}"

def run_tool(name: str, inputs: dict) -> str:
    if name == "search":
        return search(**inputs)
    if name == "calculator":
        return calculator(**inputs)
    return f"Unknown tool: {name}"

# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def _make_headers() -> dict:
    if cfg["format"] == "anthropic":
        return {
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

def _to_openai_tools(tools: list) -> list:
    """Convert Anthropic tool schema → OpenAI function schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]

def _build_request(messages: list) -> dict:
    if cfg["format"] == "anthropic":
        return {
            "model": cfg["model"],
            "max_tokens": 4096,
            "tools": TOOLS,
            "messages": messages,
        }
    return {
        "model": cfg["model"],
        "max_tokens": 4096,
        "tools": _to_openai_tools(TOOLS),
        "messages": messages,
    }

def _get_tokens(response: dict) -> dict:
    """Normalise token counts across providers."""
    usage = response.get("usage", {})
    if cfg["format"] == "anthropic":
        return {
            "input_tokens":  usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }
    return {
        "input_tokens":  usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }

def _annotate_llm_span(span, messages: list, data: dict, texts: list) -> None:
    last_user = next(
        (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"),
        None,
    )
    input_text = last_user.get("content", "") if last_user else ""
    if not isinstance(input_text, str):
        input_text = json.dumps(input_text)
    LLMObs.annotate(
        span,
        input_data=[{"role": "user", "content": input_text}],
        output_data=[{"role": "assistant", "content": texts[0] if texts else ""}],
        metrics=_get_tokens(data),
    )

def _call_model(messages: list) -> dict:
    resp = requests.post(cfg["url"], headers=_make_headers(), json=_build_request(messages))
    resp.raise_for_status()
    return resp.json()

def _parse(response: dict):
    """
    Normalise provider response into:
      stop      "end_turn" | "tool_use"
      assistant  dict to append to message history
      texts      list of text strings from the model
      calls      list of (tool_use_id, name, inputs_dict)
    """
    if cfg["format"] == "anthropic":
        stop      = response["stop_reason"]
        content   = response["content"]
        texts     = [b["text"] for b in content if b["type"] == "text"]
        calls     = [
            (b["id"], b["name"], b["input"])
            for b in content if b["type"] == "tool_use"
        ]
        assistant = {"role": "assistant", "content": content}

    else:  # openai / deepseek / groq / ollama
        choice = response["choices"][0]
        msg    = choice["message"]
        finish = choice["finish_reason"]
        stop   = "end_turn" if finish == "stop" else "tool_use"
        texts  = [msg["content"]] if msg.get("content") else []
        calls  = [
            (tc["id"], tc["function"]["name"], json.loads(tc["function"]["arguments"]))
            for tc in (msg.get("tool_calls") or [])
        ]
        assistant = {k: msg[k] for k in ("role", "content", "tool_calls") if k in msg}

    return stop, assistant, texts, calls

def _tool_result_messages(results: list) -> list:
    """
    results: list of (tool_use_id, name, inputs, result_str)
    Returns the message(s) to append to history.
    """
    if cfg["format"] == "anthropic":
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": id_, "content": res}
            for id_, _, _, res in results
        ]}]
    return [
        {"role": "tool", "tool_call_id": id_, "content": res}
        for id_, _, _, res in results
    ]

# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------
def _step(messages: list):
    """One model call → parsed result. Wrapped in a DD LLM span when enabled."""
    if DD_ENABLED:
        with LLMObs.llm(model_name=cfg["model"], model_provider=PROVIDER, name="llm_call") as span:
            data = _call_model(messages)
            parsed = _parse(data)
            _annotate_llm_span(span, messages, data, parsed[2])
            return parsed
    return _parse(_call_model(messages))

def _invoke_tool(name: str, inputs: dict) -> str:
    if DD_ENABLED:
        with LLMObs.tool(name=name) as span:
            result = run_tool(name, inputs)
            LLMObs.annotate(span, input_data=inputs, output_data=result)
            return result
    return run_tool(name, inputs)

def _loop(messages: list) -> str:
    final_answer = ""
    while True:
        stop, assistant, texts, calls = _step(messages)
        messages.append(assistant)

        for text in texts:
            if text.strip():
                print(f"\nAgent: {text.strip()}")
                final_answer = text.strip()

        if stop == "end_turn":
            return final_answer

        results = []
        for id_, name, inputs in calls:
            print(f"\n  -> {name}({json.dumps(inputs)})")
            result = _invoke_tool(name, inputs)
            print(f"     = {result}")
            results.append((id_, name, inputs, result))
        for msg in _tool_result_messages(results):
            messages.append(msg)

def run_agent(task: str) -> str:
    messages = [{"role": "user", "content": task}]
    print(f"\n[{PROVIDER} / {cfg['model']}]  Task: {task}\n{'-' * 50}")

    if DD_ENABLED:
        with LLMObs.agent(name="simple-agent") as span:
            final_answer = _loop(messages)
            LLMObs.annotate(span, input_data=task, output_data=final_answer)
    else:
        final_answer = _loop(messages)

    print(f"\n{'-' * 50}")
    return final_answer

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Task: ").strip()
    if not task:
        print("No task provided.")
        sys.exit(1)
    run_agent(task)
