"""
Competitor Intelligence Agent
---------------------------------
Run:  python agent.py
      python agent.py "Salesforce"

The agent autonomously searches the web, fetches pages, and produces a
self-contained HTML intelligence report saved to disk.
"""

import re
import sys
import requests
from bs4 import BeautifulSoup
import anthropic

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"

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
            url = r.select_one(".result__url")
            if title and snippet:
                results.append(
                    f"Title: {title.get_text(strip=True)}\n"
                    f"URL: {url.get_text(strip=True) if url else 'N/A'}\n"
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


def execute_tool(name: str, inputs: dict) -> str:
    """Dispatch a tool call to the correct implementation."""
    if name == "web_search":
        return web_search(**inputs)
    if name == "fetch_page":
        return fetch_page(**inputs)
    if name == "save_report":
        return save_report(**inputs)
    return f"Unknown tool: {name}"


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
    print(f"{'='*60}\n")

    while True:
        turn += 1
        print(f"[Turn {turn}] Calling Claude ({MODEL})...")

        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        usage = response.usage
        print(f"[Turn {turn}] Stop reason: {response.stop_reason}  |  "
              f"Tokens in: {usage.input_tokens}  out: {usage.output_tokens}")

        # Print any text the model produced
        for block in response.content:
            if hasattr(block, "type") and block.type == "text" and block.text.strip():
                snippet = block.text.strip()[:200].replace("\n", " ")
                print(f"[Turn {turn}] Assistant: {snippet}{'...' if len(block.text.strip()) > 200 else ''}")

        # Append the assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # ── Done ──────────────────────────────────────────────────────────
        if response.stop_reason == "end_turn":
            print(f"[Turn {turn}] Agent finished.\n")
            break

        # ── Tool call(s) needed ───────────────────────────────────────────
        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

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

        # Unexpected stop reason — bail out
        print(f"[Turn {turn}] Unexpected stop_reason: {response.stop_reason}")
        break

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
