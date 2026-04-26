"""
HG Catalyst Hackathon — Building an Autonomous Agent in 30 Minutes
===================================================================
This file walks through 6 progressive steps, each a self-contained runnable
script. Uncomment the step you want to run and execute:  python workshop.py

STEP 1 — Hello World           ~3 min
STEP 2 — Define a tool         ~5 min
STEP 3 — Execute the tool      ~7 min
STEP 4 — The agent loop        ~8 min
STEP 5 — Full agent            ~7 min
STEP 6 — Add observability     ~5 min  (bonus)
"""

import json
import os
import re
import sys
import anthropic

client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
MODEL  = "claude-sonnet-4-6"


# ===========================================================================
# STEP 1 — Hello World
# ===========================================================================
# The simplest possible call: send a message, get a response.
# Key concept: client.messages.create() → response.content[0].text
# ===========================================================================

def step1_hello_world():
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[
            {"role": "user", "content": "What is a competitor intelligence report?"}
        ],
    )
    print(response.content[0].text)


# ===========================================================================
# STEP 2 — Define a tool
# ===========================================================================
# Give Claude a tool. Notice stop_reason == "tool_use" — Claude wants to
# call the tool but we haven't executed anything yet.
# Key concept: tool JSON schema, stop_reason
# ===========================================================================

FETCH_PAGE_TOOL = {
    "type": "custom",
    "name": "fetch_page",
    "description": "Fetch the text content of a web page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"}
        },
        "required": ["url"],
    },
}

def step2_define_a_tool():
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[FETCH_PAGE_TOOL],
        messages=[
            {"role": "user", "content": "Fetch the Wikipedia page for Salesforce."}
        ],
    )

    print("stop_reason:", response.stop_reason)   # → "tool_use"
    for block in response.content:
        print(block)                               # ToolUseBlock with name + input


# ===========================================================================
# STEP 3 — Execute the tool
# ===========================================================================
# Now we actually run the tool and send the result back to Claude.
# Key concept: tool_result message, the full request → response → execute → return cycle
# ===========================================================================

import requests
from bs4 import BeautifulSoup

def _fetch_page(url: str) -> str:
    """Real implementation used in steps 3–5."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:4000]
    except Exception as exc:
        return f"Error: {exc}"

def step3_execute_the_tool():
    messages = [
        {"role": "user", "content": "Fetch the Wikipedia page for Salesforce."}
    ]

    # Turn 1 — Claude decides to call the tool
    response = client.messages.create(
        model=MODEL, max_tokens=1024, tools=[FETCH_PAGE_TOOL], messages=messages
    )
    messages.append({"role": "assistant", "content": response.content})

    print("Turn 1 stop_reason:", response.stop_reason)  # tool_use

    # Execute every tool Claude asked for
    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            print(f"Calling: {block.name}({block.input})")
            result = _fetch_page(**block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

    # Turn 2 — send results back
    messages.append({"role": "user", "content": tool_results})
    response2 = client.messages.create(
        model=MODEL, max_tokens=1024, tools=[FETCH_PAGE_TOOL], messages=messages
    )

    print("\nClaude's answer:")
    print(response2.content[0].text)


# ===========================================================================
# STEP 4 — The agent loop
# ===========================================================================
# Wrap the round-trip in a while loop so Claude can call tools as many times
# as it needs.
# Key concept: while True, stop_reason == "end_turn" breaks the loop
# ===========================================================================

def step4_agent_loop():
    messages = [
        {
            "role": "user",
            "content": (
                "Fetch the Wikipedia pages for both Salesforce and HubSpot, "
                "then write a short paragraph comparing them."
            ),
        }
    ]

    while True:
        response = client.messages.create(
            model=MODEL, max_tokens=2048, tools=[FETCH_PAGE_TOOL], messages=messages
        )
        messages.append({"role": "assistant", "content": response.content})

        print(f"stop_reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            print("\nFinal answer:")
            for block in response.content:
                if hasattr(block, "text"):
                    print(block.text)
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  → {block.name}({list(block.input.keys())})")
                    result = _fetch_page(**block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})


# ===========================================================================
# STEP 5 — Full agent (same as competitor-intelligence-agent.py, condensed for teaching)
# ===========================================================================
# Add web_search (server-side) + save_report, a proper system prompt,
# and handle the pause_turn stop reason.
# Key concept: mixing server-side tools with custom tools, system prompts
# ===========================================================================

TOOLS_FULL = [
    {"type": "web_search_20260209", "name": "web_search"},
    {
        "type": "custom",
        "name": "fetch_page",
        "description": "Fetch the full text of a web page.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "type": "custom",
        "name": "save_report",
        "description": "Save the finished HTML report to disk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "html":     {"type": "string"},
            },
            "required": ["filename", "html"],
        },
    },
]

SYSTEM_PROMPT = """You are a competitive intelligence analyst.
Given a company name:
1. Run at least 4 web searches about the company and its competitors.
2. Fetch 1–2 detailed pages.
3. Write a short but polished HTML report (Tailwind CSS CDN) with:
   - A header, "About" section, competitor cards, and key takeaways.
4. Call save_report once with the complete HTML.
"""

def _save_report(filename: str, html: str) -> str:
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    return f"Saved to {filename}"

def _execute_tool(name: str, inputs: dict) -> str:
    if name == "fetch_page":  return _fetch_page(**inputs)
    if name == "save_report": return _save_report(**inputs)
    return f"Unknown tool: {name}"

def step5_full_agent():
    company = input("Company name: ").strip()
    messages = [{"role": "user", "content": f"Research: {company}"}]
    saved = None

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            tools=TOOLS_FULL,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        print(f"stop_reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "pause_turn":        # web_search finished
            messages.append({"role": "user", "content": []})
            continue

        if response.stop_reason == "tool_use":
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                print(f"  → {block.name}")
                result = _execute_tool(block.name, block.input)
                if block.name == "save_report":
                    saved = block.input.get("filename")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": results})

    print(f"\nDone! Open: {saved}" if saved else "\nFinished (no file saved).")


# ===========================================================================
# STEP 6 — Add observability (bonus)
# ===========================================================================
# Wrap the agent loop with Datadog LLM Observability so you can see every
# LLM call, tool execution, and the full agent trace in a dashboard.
# Key concept: LLMObs.agent / .llm / .tool spans, annotate()
#
# Requires:  pip install ddtrace
# Env vars:  DD_API_KEY, DD_SITE (defaults to us5.datadoghq.com)
# ===========================================================================

def step6_observability():
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(
        ml_app=os.environ.get("DD_LLMOBS_ML_APP", "catalyst-workshop"),
        api_key=os.environ.get("DD_API_KEY"),
        site=os.environ.get("DD_SITE", "us5.datadoghq.com"),
        agentless_enabled=True,
    )

    company = input("Company name: ").strip()
    messages = [{"role": "user", "content": f"Research: {company}"}]
    saved = None

    with LLMObs.agent(name="competitor-intel") as agent_span:
        while True:
            # Wrap the LLM call in a span
            with LLMObs.llm(
                model_name=MODEL,
                model_provider="anthropic",
                name="llm_call",
            ) as llm_span:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=8000,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS_FULL,
                    messages=messages,
                )
                out_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                LLMObs.annotate(
                    llm_span,
                    input_data=messages,
                    output_data=[{"role": "assistant", "content": out_text}],
                    metrics={
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                )

            messages.append({"role": "assistant", "content": response.content})
            print(f"stop_reason: {response.stop_reason}")

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "pause_turn":
                continue

            if response.stop_reason == "tool_use":
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    print(f"  → {block.name}")
                    # Wrap each tool call in a span
                    with LLMObs.tool(name=block.name) as tool_span:
                        result = _execute_tool(block.name, block.input)
                        LLMObs.annotate(
                            tool_span,
                            input_data=block.input,
                            output_data=result,
                        )
                    if block.name == "save_report":
                        saved = block.input.get("filename")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                messages.append({"role": "user", "content": results})

        LLMObs.annotate(
            agent_span,
            input_data=f"Research: {company}",
            output_data=f"Saved to {saved}" if saved else "No file saved",
        )

    print(f"\nDone! Open: {saved}" if saved else "\nFinished (no file saved).")
    print("View traces: https://us5.datadoghq.com/llm/traces?query=@ml_app:catalyst-workshop")


# ===========================================================================
# Runner — change STEP to run a different step
# ===========================================================================

STEP = 1          # ← change this to 1, 2, 3, 4, 5, or 6

if __name__ == "__main__":
    steps = {
        1: step1_hello_world,
        2: step2_define_a_tool,
        3: step3_execute_the_tool,
        4: step4_agent_loop,
        5: step5_full_agent,
        6: step6_observability,
    }
    fn = steps.get(STEP)
    if fn:
        print(f"{'='*60}\nRunning Step {STEP}\n{'='*60}\n")
        fn()
    else:
        print(f"Unknown step: {STEP}. Choose 1–6.")
