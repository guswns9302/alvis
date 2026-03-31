#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "brew is required to install tmux automatically" >&2
  exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
  brew install tmux
fi

if [ ! -d "${ROOT_DIR}/.venv" ]; then
  python3 -m venv "${ROOT_DIR}/.venv"
fi

source "${ROOT_DIR}/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

python - <<'PY'
mods = ["typer", "fastapi", "sqlalchemy", "pydantic", "structlog", "pytest"]
for name in mods:
    __import__(name)
print("bootstrap verification ok")
PY

echo "Bootstrap complete."
