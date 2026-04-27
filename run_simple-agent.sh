#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Load .env if it exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Point SSL to certifi's bundle so ddtrace can reach Datadog on all Python setups
SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())" 2>/dev/null || true)
[ -n "$SSL_CERT_FILE" ] && export SSL_CERT_FILE

# Run with Datadog LLM Observability if DD_API_KEY is set, otherwise run plain
if [ -n "${DD_API_KEY:-}" ]; then
    DD_SITE="us5.datadoghq.com" \
    DD_LLMOBS_ENABLED=1 \
    DD_LLMOBS_ML_APP=simple-agent \
    ddtrace-run python3 react_agent.py "$@"
else
    python3 react_agent.py "$@"
fi
