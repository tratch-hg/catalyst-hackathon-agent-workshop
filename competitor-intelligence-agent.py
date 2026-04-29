"""
Competitor Intelligence Agent
---------------------------------
Run:  python competitor-intelligence-agent.py
      python competitor-intelligence-agent.py "Salesforce"

The agent autonomously searches the web, fetches pages, and produces a
self-contained HTML intelligence report saved to disk.
"""

import json
import os
import re
import sys
from contextlib import nullcontext
from urllib.parse import parse_qs, unquote, urlparse
import requests
from bs4 import BeautifulSoup
import anthropic

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
    LLMObs.enable(
        ml_app=os.environ.get("DD_LLMOBS_ML_APP", "competitor-intelligence-agent"),
        api_key=os.environ.get("DD_API_KEY"),
        site=os.environ.get("DD_SITE", "us5.datadoghq.com"),
        agentless_enabled=True,
        integrations_enabled=False,
    )

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 32000  # streamed; the final report HTML can be large


def _call_claude(messages: list):
    """Stream a turn and return the final Message. Streaming is required
    above ~16k max_tokens, and report HTML can easily blow past that."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
    ) as stream:
        return stream.get_final_message()

# ---------------------------------------------------------------------------
# Tool definitions  (plain JSON — exactly what Claude sees)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "custom",
        "name": "web_search",
        "description": (
            "Search the web for information about a topic. "
            "Returns the top search results with titles, URLs, and snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "type": "custom",
        "name": "fetch_page",
        "description": (
            "Fetch the full text content of a web page. "
            "Use this after web_search to read specific pages in detail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL of the page to fetch.",
                }
            },
            "required": ["url"],
        },
    },
    {
        "type": "custom",
        "name": "save_report",
        "description": (
            "Save the finished HTML competitor intelligence report to disk. "
            "Call this ONCE at the end with a complete, polished HTML document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Output filename, e.g. salesforce_intelligence.html",
                },
                "html": {
                    "type": "string",
                    "description": "The full HTML content of the report.",
                },
            },
            "required": ["filename", "html"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _unwrap_ddg_url(href: str) -> str:
    """DuckDuckGo wraps result links in `//duckduckgo.com/l/?uddg=<encoded-target>`.
    Pull the real target out so fetch_page receives a usable URL."""
    if not href:
        return "N/A"
    normalized = "https:" + href if href.startswith("//") else href
    parsed = urlparse(normalized)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return normalized


def web_search(query: str) -> str:
    """Search DuckDuckGo and return top results (no API key required)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for r in soup.select(".result")[:6]:
            title = r.select_one(".result__title")
            snippet = r.select_one(".result__snippet")
            url = r.select_one(".result__a")
            if title and snippet:
                results.append(
                    f"Title: {title.get_text(strip=True)}\n"
                    f"URL: {_unwrap_ddg_url(url.get('href')) if url else 'N/A'}\n"
                    f"Snippet: {snippet.get_text(strip=True)}"
                )
        return "\n\n".join(results) if results else "No results found."
    except Exception as exc:
        return f"Search error: {exc}"


def fetch_page(url: str) -> str:
    """Fetch a URL and return cleaned text (capped at 6000 chars)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:6000]
    except Exception as exc:
        return f"Error fetching {url}: {exc}"


def save_report(filename: str, html: str) -> str:
    """Write the HTML report to disk."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    return f"Report saved to {filename}"


def _execute_tool_impl(name: str, inputs: dict) -> str:
    """Dispatch a tool call to the correct implementation."""
    if name == "web_search":
        return web_search(**inputs)
    if name == "fetch_page":
        return fetch_page(**inputs)
    if name == "save_report":
        return save_report(**inputs)
    return f"Unknown tool: {name}"


def execute_tool(name: str, inputs: dict) -> str:
    """Execute a tool, wrapped in a Datadog span with tool-specific metrics."""
    if not DD_ENABLED:
        return _execute_tool_impl(name, inputs)

    with LLMObs.tool(name=name) as span:
        result = _execute_tool_impl(name, inputs)
        is_error = result.startswith(("Error fetching", "Search error:", "Unknown tool:"))
        metrics: dict = {}
        tags: dict = {"error": "true" if is_error else "false"}

        if name == "web_search":
            metrics["result_count"] = result.count("Title:")
            tags["query"] = inputs.get("query", "")[:100]
        elif name == "fetch_page":
            metrics["chars_fetched"] = 0 if is_error else len(result)
            tags["url"] = inputs.get("url", "")[:150]
        elif name == "save_report":
            metrics["html_size_bytes"] = len(inputs.get("html", ""))
            tags["filename"] = inputs.get("filename", "")

        LLMObs.annotate(span, input_data=inputs, output_data=result, metrics=metrics, tags=tags)
        return result


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert competitive intelligence analyst.

When given a company name, you will:
1. Run at least 6 web searches covering:
   - The company overview, products, and positioning
   - Key competitors and market landscape
   - Recent news, funding, or strategic moves
   - Pricing and feature comparisons
   - Customer reviews and sentiment
   - Future roadmap or strategic direction
2. Fetch 2–3 specific pages that contain the most useful detail.
3. Synthesise everything into a polished HTML report using Tailwind CSS (CDN).

The HTML report must include:
- A hero header with the company name and a one-line summary
- An "About" section
- A "Competitors" section with a card for each major competitor
  (logo placeholder, name, tagline, key strengths, weaknesses)
- A feature comparison table (target company vs top 3 competitors)
- A "Strategic Takeaways" section with 3–5 bullet insights
- A footer with "Generated by Competitor Intelligence Agent"

Use only Tailwind utility classes. Do NOT use any external images.
Make the page visually appealing with colour, spacing, and typography.

When the report is complete, call save_report with an appropriate filename
(snake_case company name + "_intelligence.html") and the full HTML string.
"""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(company: str) -> None:
    messages = [{"role": "user", "content": f"Research this company: {company}"}]
    saved_file = None
    turn = 0

    print(f"\n{'='*60}")
    print(f"  Competitor Intelligence Agent")
    print(f"  Target: {company}")
    if DD_ENABLED:
        print(f"  Datadog LLM Observability: ON")
    print(f"{'='*60}\n")

    def _loop():
        nonlocal saved_file, turn

        while True:
            turn += 1
            print(f"[Turn {turn}] Calling Claude ({MODEL})...")

            with (LLMObs.workflow(name="agent_turn") if DD_ENABLED else nullcontext()) as turn_span:
                if DD_ENABLED:
                    with LLMObs.llm(
                        model_name=MODEL,
                        model_provider="anthropic",
                        name="llm_call",
                    ) as llm_span:
                        response = _call_claude(messages)
                        out_text = next(
                            (b.text for b in response.content if hasattr(b, "text")), ""
                        )
                        last_user = next(
                            (m for m in reversed(messages) if m.get("role") == "user"),
                            None,
                        )
                        input_text = last_user.get("content", "") if last_user else ""
                        if not isinstance(input_text, str):
                            input_text = json.dumps(input_text, default=str)
                        LLMObs.annotate(
                            llm_span,
                            input_data=[{"role": "user", "content": input_text}],
                            output_data=[{"role": "assistant", "content": out_text}],
                            metrics={
                                "input_tokens": response.usage.input_tokens,
                                "output_tokens": response.usage.output_tokens,
                            },
                            tags={
                                "turn": str(turn),
                                "stop_reason": response.stop_reason,
                            },
                        )
                else:
                    response = _call_claude(messages)

                usage = response.usage
                print(f"[Turn {turn}] Stop reason: {response.stop_reason}  |  "
                      f"Tokens in: {usage.input_tokens}  out: {usage.output_tokens}")

                for block in response.content:
                    if hasattr(block, "type") and block.type == "text" and block.text.strip():
                        snippet = block.text.strip()[:200].replace("\n", " ")
                        print(f"[Turn {turn}] Assistant: {snippet}{'...' if len(block.text.strip()) > 200 else ''}")

                messages.append({"role": "assistant", "content": response.content})

                tool_blocks = [b for b in response.content if hasattr(b, "type") and b.type == "tool_use"]

                if turn_span is not None:
                    LLMObs.annotate(
                        turn_span,
                        tags={
                            "turn": str(turn),
                            "stop_reason": response.stop_reason,
                            "tools_called": str(len(tool_blocks)),
                        },
                    )

                if response.stop_reason == "end_turn":
                    print(f"[Turn {turn}] Agent finished.\n")
                    break

                if response.stop_reason == "max_tokens":
                    print(
                        f"[Turn {turn}] Hit max_tokens ({MAX_TOKENS}). "
                        "Bump MAX_TOKENS or tighten the system prompt."
                    )
                    break

                if response.stop_reason == "tool_use":
                    tool_results = []

                    for block in tool_blocks:
                        if block.name == "fetch_page":
                            detail = block.input.get("url", "")
                        elif block.name == "save_report":
                            detail = block.input.get("filename", "")
                        elif block.name == "web_search":
                            detail = block.input.get("query", "")
                        else:
                            detail = ", ".join(f"{k}={v!r}" for k, v in block.input.items())

                        print(f"[Turn {turn}] -> {block.name}({detail})")
                        result = execute_tool(block.name, block.input)

                        if block.name == "save_report":
                            saved_file = block.input.get("filename")
                            print(f"[Turn {turn}]    Saved: {saved_file}")
                        elif block.name == "fetch_page":
                            print(f"[Turn {turn}]    Fetched {len(result)} chars")
                        elif block.name == "web_search":
                            print(f"[Turn {turn}]    Got {result.count('Title:')} results")

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                    print()
                    messages.append({"role": "user", "content": tool_results})
                    continue

                print(f"[Turn {turn}] Unexpected stop_reason: {response.stop_reason}")
                break

    if DD_ENABLED:
        with LLMObs.agent(name="competitor-intelligence-agent") as span:
            LLMObs.annotate(span, tags={"company": company})
            _loop()
            LLMObs.annotate(
                span,
                input_data=f"Research this company: {company}",
                output_data=f"Report saved to {saved_file}" if saved_file else "No report saved",
                tags={
                    "company": company,
                    "total_turns": str(turn),
                    "report_saved": "true" if saved_file else "false",
                },
            )
    else:
        _loop()

    if saved_file:
        print(f"{'='*60}")
        print(f"  Done! Report saved to: {saved_file}")
        print(f"  Open it in your browser: open {saved_file}")
        print(f"{'='*60}\n")
    else:
        print("\nAgent finished (no report was saved).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        company_name = " ".join(sys.argv[1:])
    else:
        company_name = input("Enter a company name: ").strip()

    if not company_name:
        print("No company name provided. Exiting.")
        sys.exit(1)

    run_agent(company_name)

    if DD_ENABLED:
        try:
            LLMObs.disable()
        except Exception:
            pass
