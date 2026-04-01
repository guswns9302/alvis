#!/usr/bin/env bash
set -euo pipefail

ALVIS_HOME="${ALVIS_HOME:-$HOME/.alvis}"
ALVIS_RELEASE_REPO="${ALVIS_RELEASE_REPO:-guswns9302/alvis}"
ALVIS_VERSION="${ALVIS_VERSION:-latest}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required" >&2
    exit 1
  fi
}

require_cmd python3
require_cmd curl
require_cmd tar
require_cmd codex

CODEX_PATH="$(command -v codex)"

mkdir -p "$ALVIS_HOME"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [ "$ALVIS_VERSION" = "latest" ]; then
  RELEASE_JSON="$(curl -fsSL "https://api.github.com/repos/${ALVIS_RELEASE_REPO}/releases/latest")"
else
  RELEASE_JSON="$(curl -fsSL "https://api.github.com/repos/${ALVIS_RELEASE_REPO}/releases/tags/${ALVIS_VERSION}")"
fi

TARBALL_URL="$(printf '%s' "$RELEASE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tarball_url"])')"
TAG_NAME="$(printf '%s' "$RELEASE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"

curl -fsSL "$TARBALL_URL" -o "$TMP_DIR/alvis.tar.gz"
mkdir -p "$TMP_DIR/src"
tar -xzf "$TMP_DIR/alvis.tar.gz" -C "$TMP_DIR/src"
EXTRACTED_DIR="$(find "$TMP_DIR/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

ALVIS_EXTRACTED_DIR="$EXTRACTED_DIR" \
ALVIS_RELEASE_REPO="$ALVIS_RELEASE_REPO" \
ALVIS_TAG_NAME="$TAG_NAME" \
ALVIS_TARBALL_URL="$TARBALL_URL" \
ALVIS_CODEX_COMMAND="$CODEX_PATH" \
ALVIS_HOME="$ALVIS_HOME" \
PYTHONPATH="$EXTRACTED_DIR" \
python3 - <<'PY'
import os
from pathlib import Path

from app.config import get_settings
from app.upgrade import install_from_source

settings = get_settings(Path.cwd()).model_copy(
    update={
        "app_home": Path(os.environ["ALVIS_HOME"]).expanduser(),
        "codex_command": os.environ["ALVIS_CODEX_COMMAND"],
        "release_repo": os.environ["ALVIS_RELEASE_REPO"],
    }
)
install_from_source(
    settings,
    Path(os.environ["ALVIS_EXTRACTED_DIR"]),
    version=os.environ["ALVIS_TAG_NAME"],
    tarball_url=os.environ["ALVIS_TARBALL_URL"],
    codex_command=os.environ["ALVIS_CODEX_COMMAND"],
)
PY

TARGET_BIN="$HOME/.local/bin"
mkdir -p "$TARGET_BIN"
ln -sf "$ALVIS_HOME/bin/alvis" "$TARGET_BIN/alvis"
export PATH="$TARGET_BIN:$PATH"
echo
"$ALVIS_HOME/bin/alvis" doctor

echo "Alvis installed."
echo "Version: $TAG_NAME"
echo "Home: $ALVIS_HOME"
echo "Binary: $TARGET_BIN/alvis"
echo "Next steps:"
echo "  1. alvis doctor"
echo "  2. alvis start"
echo "If '$HOME/.local/bin' is not on PATH, add it to your shell profile."
