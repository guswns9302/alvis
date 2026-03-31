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

ensure_tmux() {
  if command -v tmux >/dev/null 2>&1; then
    return 0
  fi
  echo "tmux is not installed. attempting Homebrew install..."
  if ! command -v brew >/dev/null 2>&1; then
    echo "tmux is required, and Homebrew is not available to install it automatically." >&2
    echo "Install Homebrew first or install tmux manually, then rerun install.sh." >&2
    exit 1
  fi
  brew install tmux
}

require_cmd python3
require_cmd curl
require_cmd tar
ensure_tmux
require_cmd codex

TMUX_PATH="$(command -v tmux)"
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

rm -rf "$ALVIS_HOME/app"
mkdir -p "$ALVIS_HOME"
cp -R "$EXTRACTED_DIR" "$ALVIS_HOME/app"

if [ ! -d "$ALVIS_HOME/venv" ]; then
  python3 -m venv "$ALVIS_HOME/venv"
fi

"$ALVIS_HOME/venv/bin/python" -m pip install --upgrade pip
"$ALVIS_HOME/venv/bin/python" -m pip install "$ALVIS_HOME/app"

mkdir -p "$ALVIS_HOME/bin"
cat > "$ALVIS_HOME/bin/alvis" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export ALVIS_HOME="$ALVIS_HOME"
export ALVIS_TMUX_PATH="$TMUX_PATH"
export ALVIS_CODEX_COMMAND="$CODEX_PATH"
exec "$ALVIS_HOME/venv/bin/alvis" "\$@"
EOF
chmod +x "$ALVIS_HOME/bin/alvis"

TARGET_BIN="$HOME/.local/bin"
mkdir -p "$TARGET_BIN"
ln -sf "$ALVIS_HOME/bin/alvis" "$TARGET_BIN/alvis"

cat > "$ALVIS_HOME/install.json" <<EOF
{
  "version": "$TAG_NAME",
  "release_repo": "$ALVIS_RELEASE_REPO",
  "installed_via": "install.sh",
  "tmux_path": "$TMUX_PATH",
  "codex_command": "$CODEX_PATH"
}
EOF

export PATH="$TARGET_BIN:$PATH"
"$ALVIS_HOME/bin/alvis" daemon start >/dev/null 2>&1 || true

echo "Alvis installed."
echo "Version: $TAG_NAME"
echo "Home: $ALVIS_HOME"
echo "Binary: $TARGET_BIN/alvis"
echo "If '$HOME/.local/bin' is not on PATH, add it to your shell profile."
