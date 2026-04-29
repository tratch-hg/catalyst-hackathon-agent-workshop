"""
HG Catalyst Hackathon — Building an Autonomous Agent in 30 Minutes
===================================================================
This file walks through 6 progressive steps, each a self-contained runnable
script. Run it and pick a step from the menu, or pass the step number directly:

    python workshop.py        # interactive menu
    python workshop.py 3      # run step 3 directly

STEP 1 — Hello World           ~3 min
STEP 2 — Define a tool         ~5 min
STEP 3 — Execute the tool      ~7 min
STEP 4 — The agent loop        ~8 min
STEP 5 — Full agent            ~7 min
STEP 6 — Add observability     ~5 min  (bonus)

HOW THE ANTHROPIC API WORKS (read this first)
---------------------------------------------
The API is stateless — Claude has no memory between calls. You maintain the
conversation yourself as a list of message dicts:

    messages = [
        {"role": "user",      "content": "..."},
        {"role": "assistant", "content": [...]},   # Claude's response blocks
        {"role": "user",      "content": [...]},   # tool results go here
    ]

Each call to client.messages.create() takes the full list and appends Claude's
reply. You then append that reply to your list and loop. This is the core
pattern that every agent is built on.

PREREQUISITES
-------------
    pip install anthropic requests beautifulsoup4
    export ANTHROPIC_API_KEY=sk-...

For Step 6 (observability):
    pip install ddtrace
    export DD_API_KEY=...
"""

import json
import os
import re
import sys
import anthropic

# The client reads ANTHROPIC_API_KEY from the environment automatically.
# Pinning the model name here keeps every step consistent and makes it easy
# to swap models for experimentation.
client = anthropic.Anthropic()
MODEL  = "claude-sonnet-4-6"


# ===========================================================================
# STEP 1 — Hello World
# ===========================================================================
# The simplest possible API call: one user message, one assistant reply.
#
# Key concepts:
#   client.messages.create()  — the main API method
#   max_tokens                — upper bound on the response length (required)
#   response.content          — list of content blocks (TextBlock, ToolUseBlock…)
#   response.content[0].text  — the reply text for a simple non-tool response
#
# Try it: change the question or set max_tokens=32 to see a truncated reply.
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
# Tools extend Claude with abilities it doesn't have natively (browsing,
# databases, calculators, APIs, …). You describe a tool as a JSON Schema and
# Claude decides when and how to call it.
#
# Tool schema anatomy:
#   type          — "custom" for tools you implement; other values like
#                   "web_search_20260209" are server-side tools Anthropic runs.
#   name          — identifier Claude uses when it wants to call the tool
#   description   — plain-English explanation that guides Claude's decision
#   input_schema  — JSON Schema object describing the arguments Claude must pass
#
# After this call stop_reason == "tool_use", meaning Claude wants to use a tool
# but has NOT actually executed anything. The execution is your responsibility
# (see Step 3). This separation lets you validate, log, or reject calls first.
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

    # stop_reason tells you WHY Claude stopped generating:
    #   "end_turn"  — Claude finished its reply naturally (done)
    #   "tool_use"  — Claude wants to call one or more tools (you must execute them)
    #   "pause_turn"— a server-side tool (e.g. web_search) is running; resume the loop
    #   "max_tokens"— ran out of token budget (increase max_tokens or chunk your work)
    print("stop_reason:", response.stop_reason)   # → "tool_use"

    # response.content is a list of blocks. With tools enabled you may get a mix of:
    #   TextBlock(text="…")                — Claude's thinking / narration
    #   ToolUseBlock(id, name, input={…})  — a tool call Claude wants to make
    for block in response.content:
        print(block)


# ===========================================================================
# STEP 3 — Execute the tool
# ===========================================================================
# The full one-shot round-trip: Claude asks → you run the tool → Claude reads
# the result and replies.
#
# The key pattern is the tool_result message:
#
#   messages.append({"role": "user", "content": [
#       {
#           "type":        "tool_result",
#           "tool_use_id": block.id,    # ties the result to the right tool call
#           "content":     "… result …",
#       }
#   ]})
#
# Claude can request multiple tools in a single turn. Always loop over all
# content blocks and collect every tool_result before sending the next message.
# Sending them in one batch is more efficient and avoids confusing Claude.
# ===========================================================================

import requests
from bs4 import BeautifulSoup

def _fetch_page(url: str) -> str:
    """Fetch a URL, strip boilerplate HTML, and return up to 4 000 chars of text."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Remove non-content tags so Claude receives clean prose, not nav/footer noise.
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Truncate to keep the result within Claude's context window comfortably.
        return text[:4000]
    except Exception as exc:
        # Return errors as strings — Claude reads tool_result content and will
        # adapt (e.g. try a different URL) rather than crashing.
        return f"Error: {exc}"

def step3_execute_the_tool():
    messages = [
        {"role": "user", "content": "Fetch the Wikipedia page for Salesforce."}
    ]

    # Turn 1 — Claude sees the user message and decides to call fetch_page
    response = client.messages.create(
        model=MODEL, max_tokens=1024, tools=[FETCH_PAGE_TOOL], messages=messages
    )
    # Always append the full response.content (not just the text) so the
    # conversation history includes the ToolUseBlock that Claude sent.
    messages.append({"role": "assistant", "content": response.content})

    print("Turn 1 stop_reason:", response.stop_reason)  # tool_use

    # Execute every tool Claude asked for and collect the results.
    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            print(f"Calling: {block.name}({block.input})")
            result = _fetch_page(**block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,   # must match the ToolUseBlock id exactly
                "content": result,
            })

    # Turn 2 — send all tool results back in a single user message, then let
    # Claude generate its final answer.
    messages.append({"role": "user", "content": tool_results})
    response2 = client.messages.create(
        model=MODEL, max_tokens=1024, tools=[FETCH_PAGE_TOOL], messages=messages
    )

    print("\nClaude's answer:")
    print(response2.content[0].text)


# ===========================================================================
# STEP 4 — The agent loop
# ===========================================================================
# Real tasks need multiple tool calls in sequence (search → read → search
# again based on what you found → …). A while-True loop handles this: keep
# going until Claude says it's done ("end_turn").
#
# Agent loop skeleton:
#
#   while True:
#       response = client.messages.create(…, messages=messages)
#       messages.append({"role": "assistant", "content": response.content})
#
#       if response.stop_reason == "end_turn":
#           break                      # Claude is satisfied — exit the loop
#
#       if response.stop_reason == "tool_use":
#           results = run_all_tools(response.content)
#           messages.append({"role": "user", "content": results})
#           # Loop back → Claude reads results and decides what to do next
#
# Claude plans its own sequence of tool calls. You don't hard-code the order.
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
            # Claude decided it has enough information to answer — print and exit.
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
            # Loop back — Claude will process the results and either call more
            # tools or produce its final "end_turn" response.


# ===========================================================================
# STEP 5 — Full agent (same logic as competitor-intelligence-agent.py, condensed)
# ===========================================================================
# Three additions over Step 4:
#
#   1. System prompt   — shapes Claude's persona and sets explicit instructions
#                        (run ≥4 searches, write HTML, call save_report once).
#                        System prompts are separate from the message history
#                        and persist across every turn.
#
#   2. web_search      — a server-side tool: Anthropic executes this for you.
#                        When it runs, stop_reason == "pause_turn" and the
#                        search results arrive inside the *next* response. You
#                        just need to resume the loop with an empty user message.
#
#   3. save_report     — a custom tool you implement (_save_report below).
#                        Claude generates the full HTML and hands it to you;
#                        you write it to disk.
#
# Mixing server-side and custom tools: just include both in the tools list.
# The API routes server-side tool calls internally and surfaces the results
# automatically; custom tool calls come back as ToolUseBlocks for you to run.
# ===========================================================================

TOOLS_FULL = [
    # Server-side tool — Anthropic runs the actual web search; you just get results.
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

# The system prompt is your main lever for controlling agent behaviour.
# Be explicit: tell Claude what to do, in what order, and what the output
# format must be. Vague prompts lead to vague (and inconsistent) agents.
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
    """Dispatch a tool call by name. Add new tools here as the agent grows."""
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
            max_tokens=8000,        # generous budget — the HTML report can be long
            system=SYSTEM_PROMPT,
            tools=TOOLS_FULL,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        print(f"stop_reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "pause_turn":
            # A server-side tool (web_search) is mid-flight. Resume the loop
            # with an empty user message — the API will inject the search
            # results into the next response automatically.
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
# Without observability an agent is a black box: you see inputs and outputs
# but not what happened in between. Datadog LLM Observability captures every
# LLM call, tool execution, and the full agent trace so you can debug,
# measure latency, and track token costs.
#
# Span hierarchy mirrors your agent structure:
#
#   agent span  ("competitor-intel")        — the whole run
#     llm span  ("llm_call")               — each client.messages.create() call
#     tool span ("fetch_page" / …)         — each tool execution
#
# LLMObs.annotate() enriches a span with:
#   input_data   — what went in  (messages list, tool arguments)
#   output_data  — what came out (assistant reply, tool result)
#   metrics      — token counts, latency, cost, etc.
#
# Requires:  pip install ddtrace
# Env vars:  DD_API_KEY, DD_SITE (defaults to us5.datadoghq.com)
#            DD_LLMOBS_ML_APP — the name shown in the Datadog UI (optional)
# ===========================================================================

def step6_observability():
    from ddtrace.llmobs import LLMObs

    # agentless_enabled=True  — sends traces directly to Datadog without needing
    #                           a local Datadog Agent process running.
    # integrations_enabled=False — disables auto-patching of the anthropic SDK
    #                              so we can control spans manually (more detail).
    LLMObs.enable(
        ml_app=os.environ.get("DD_LLMOBS_ML_APP", "catalyst-workshop"),
        api_key=os.environ.get("DD_API_KEY"),
        site=os.environ.get("DD_SITE", "us5.datadoghq.com"),
        agentless_enabled=True,
        integrations_enabled=False,
    )

    company = input("Company name: ").strip()
    messages = [{"role": "user", "content": f"Research: {company}"}]
    saved = None

    # The outermost span covers the entire agent run. Everything nested inside
    # (llm spans, tool spans) appears as children in the Datadog trace view.
    with LLMObs.agent(name="competitor-intel") as agent_span:
        while True:
            # One llm span per API call so you can see token counts and latency
            # for each individual LLM invocation in the trace timeline.
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
                # Extract the text portion of Claude's response for annotation.
                # ToolUseBlocks don't have a .text attribute, hence the guard.
                out_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                LLMObs.annotate(
                    llm_span,
                    input_data=messages,
                    output_data=[{"role": "assistant", "content": out_text}],
                    metrics={
                        "input_tokens":  response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                )

            messages.append({"role": "assistant", "content": response.content})
            print(f"stop_reason: {response.stop_reason}")

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "pause_turn":
                messages.append({"role": "user", "content": []})
                continue

            if response.stop_reason == "tool_use":
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    print(f"  → {block.name}")
                    # Each tool execution gets its own span so you can measure
                    # how long fetch_page or save_report takes independently.
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

        # Annotate the top-level agent span with the overall task summary.
        LLMObs.annotate(
            agent_span,
            input_data=f"Research: {company}",
            output_data=f"Saved to {saved}" if saved else "No file saved",
        )

    print(f"\nDone! Open: {saved}" if saved else "\nFinished (no file saved).")
    print("View traces: https://us5.datadoghq.com/llm/traces?query=@ml_app:catalyst-workshop")


# ===========================================================================
# Runner
# ===========================================================================

STEPS = {
    1: ("Hello World",        step1_hello_world),
    2: ("Define a tool",      step2_define_a_tool),
    3: ("Execute the tool",   step3_execute_the_tool),
    4: ("The agent loop",     step4_agent_loop),
    5: ("Full agent",         step5_full_agent),
    6: ("Add observability",  step6_observability),
}

def _menu() -> int:
    print("\nHG Catalyst Hackathon — Workshop Steps")
    print("=" * 40)
    for num, (label, _) in STEPS.items():
        print(f"  {num}. {label}")
    print()
    while True:
        raw = input("Select a step (1–6): ").strip()
        if raw.isdigit() and int(raw) in STEPS:
            return int(raw)
        print(f"  Please enter a number between 1 and 6.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.isdigit() and int(arg) in STEPS:
            label, fn = STEPS[int(arg)]
            print(f"\n{'='*60}\nStep {arg}: {label}\n{'='*60}\n")
            fn()
            sys.exit(0)
        else:
            print(f"Usage: python workshop.py [1-6]")
            sys.exit(1)

    try:
        while True:
            step = _menu()
            label, fn = STEPS[step]
            print(f"\n{'='*60}\nStep {step}: {label}\n{'='*60}\n")
            fn()
            print()
    except KeyboardInterrupt:
        print("\nBye!")
