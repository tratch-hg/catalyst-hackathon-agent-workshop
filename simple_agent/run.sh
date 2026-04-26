#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Load .env from parent directory if it exists
if [ -f ../.env ]; then
    set -a
    source ../.env
    set +a
fi

# Datadog LLM Observability
DD_SITE="us5.datadoghq.com" \
DD_LLMOBS_ENABLED=1 \
DD_LLMOBS_ML_APP=simple-agent \
ddtrace-run python3 agent.py "$@"
