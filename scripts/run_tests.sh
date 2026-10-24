#!/usr/bin/env bash
# Lint, format check and tests on macOS / Linux. Extra arguments go to pytest.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .venv/bin/activate ]; then
  echo "ERROR: No virtual environment found. Run scripts/setup.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> ruff check"
ruff check .
echo "==> ruff format --check"
ruff format --check .
echo "==> pytest"
pytest "$@"
