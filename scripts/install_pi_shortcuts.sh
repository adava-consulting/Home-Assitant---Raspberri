#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHELL_NAME="${SHELL##*/}"

case "$SHELL_NAME" in
  zsh)
    RC_FILE="${HOME}/.zshrc"
    ;;
  bash)
    RC_FILE="${HOME}/.bashrc"
    ;;
  *)
    RC_FILE="${HOME}/.zshrc"
    ;;
esac

START_MARKER="# >>> HomeAssistant Pi shortcuts >>>"
END_MARKER="# <<< HomeAssistant Pi shortcuts <<<"

TMP_FILE="$(mktemp)"
if [[ -f "$RC_FILE" ]]; then
  awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip=1; next }
    $0 == end { skip=0; next }
    !skip { print }
  ' "$RC_FILE" > "$TMP_FILE"
fi

cat >> "$TMP_FILE" <<EOF
$START_MARKER
export HA_PI_PROJECT_DIR="${PROJECT_DIR}"
hapi() {
  "\$HA_PI_PROJECT_DIR/scripts/pi" "\$@"
}
alias hp='hapi'
alias hp-status='hapi status'
alias hp-doctor='hapi doctor'
alias hp-redeploy='hapi redeploy'
alias hp-voice='hapi voice-check'
alias hp-lights='hapi lights-check'
alias hp-sat='hapi logs satellite'
alias hp-bridge='hapi logs bridge'
alias hp-ha='hapi logs homeassistant'
$END_MARKER
EOF

mv "$TMP_FILE" "$RC_FILE"

echo "Installed Raspberry shortcuts into ${RC_FILE}"
echo "Open a new terminal or run: source ${RC_FILE}"
echo "Examples:"
echo "  hapi status"
echo "  hapi redeploy"
echo "  hapi room off --execute"
echo "  hapi all on"
