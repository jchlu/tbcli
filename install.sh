#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/tbcli.py"
EXAMPLE="$SCRIPT_DIR/config.example"

BIN_DIR="${TB_BIN_DIR:-$HOME/.local/bin}"
if [[ -n "${XDG_CONFIG_HOME:-}" && "${XDG_CONFIG_HOME}" == /* ]]; then
    CONFIG_HOME="${XDG_CONFIG_HOME%/}"
else
    CONFIG_HOME="$HOME/.config"
fi
CONFIG_DIR="$CONFIG_HOME/tbcli"
CONFIG_FILE="$CONFIG_DIR/config"

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required" >&2
    exit 1
fi

if [[ ! -f "$SOURCE" ]]; then
    echo "error: tbcli.py not found next to install.sh" >&2
    exit 1
fi

mkdir -p "$BIN_DIR"
install -m 755 "$SOURCE" "$BIN_DIR/tbcli"

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR" 2>/dev/null || true

if [[ -e "$CONFIG_FILE" ]]; then
    echo "Config already exists, leaving unchanged: $CONFIG_FILE"
elif [[ -f "$EXAMPLE" ]]; then
    install -m 600 "$EXAMPLE" "$CONFIG_FILE"
    echo "Created example config: $CONFIG_FILE"
else
    umask 077
    printf '%s\n' "TB_API=your_torbox_api_key_here" >"$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    echo "Created example config: $CONFIG_FILE"
fi

echo "Installed executable: $BIN_DIR/tbcli"

if ! printf '%s\n' "${PATH:-}" | tr ':' '\n' | grep -Fxq "$BIN_DIR"; then
    echo
    echo "Warning: $BIN_DIR is not on your PATH."
    echo "Add this to your shell profile:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
fi

echo
echo "Next: edit $CONFIG_FILE and set TB_API to your TorBox API key."
echo "Then run: tbcli links.txt"
