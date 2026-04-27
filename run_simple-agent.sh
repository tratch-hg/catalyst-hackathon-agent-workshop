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

# LLMObs initialization is handled in-process via LLMObs.enable()
python3 react_agent.py "$@"
