#!/usr/bin/env bash
# Start the app on macOS / Linux. Extra arguments go to streamlit (e.g. --server.port 8502).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .venv/bin/activate ]; then
  echo "ERROR: No virtual environment found. Run scripts/setup.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
exec python manage.py run "$@"
