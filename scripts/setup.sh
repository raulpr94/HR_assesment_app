#!/usr/bin/env bash
# Set up the app on macOS / Linux: venv, requirements, .env, database, demo data.
# Safe to re-run: existing venv, .env, database and CSV are kept.
set -euo pipefail

cd "$(dirname "$0")/.."

fail() { echo "ERROR: $*" >&2; exit 1; }

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] || fail "Python 3.10+ not found. Install it from https://www.python.org/downloads/"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python 3.10+ is required (found $("$PYTHON" --version 2>&1))."

if [ -d .venv ]; then
  echo "==> Reusing existing virtual environment (.venv)"
else
  echo "==> Creating virtual environment (.venv)"
  "$PYTHON" -m venv .venv || fail "Could not create the virtual environment. On Debian/Ubuntu: sudo apt install python3-venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing requirements"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || fail "Installing requirements failed (see above)."

if [ -f .env ]; then
  echo "==> Keeping existing .env"
else
  cp .env.example .env
  echo "==> Created .env from .env.example"
fi

echo "==> Initialising database"
python manage.py init-db
echo "==> Loading demo data"
python manage.py seed-demo

echo
echo "Setup complete. Start the app with: scripts/run_app.sh"
