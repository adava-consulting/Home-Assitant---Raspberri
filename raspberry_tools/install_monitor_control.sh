#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_USER="${HOST_USER:-lucas}"
HOST_HOME="$(eval printf '%s' "~${HOST_USER}")"
SSH_DIR="${HOST_HOME}/.ssh"
KEY_PATH="${SSH_DIR}/ha-bridge-host-action"
AUTHORIZED_KEYS="${SSH_DIR}/authorized_keys"
SUDOERS_FILE="/etc/sudoers.d/ha-monitor-power"
MONITOR_SCRIPT="${PROJECT_DIR}/raspberry_tools/monitor_power.sh"

if (( EUID != 0 )); then
  exec sudo "$0" "$@"
fi

install -d -m 700 -o "$HOST_USER" -g "$HOST_USER" "$SSH_DIR"

if [[ ! -f "$KEY_PATH" ]]; then
  ssh-keygen -t ed25519 -N '' -f "$KEY_PATH" -C "ha-bridge-host-action" >/dev/null
  chown "$HOST_USER:$HOST_USER" "$KEY_PATH" "${KEY_PATH}.pub"
fi

touch "$AUTHORIZED_KEYS"
chmod 600 "$AUTHORIZED_KEYS"
chown "$HOST_USER:$HOST_USER" "$AUTHORIZED_KEYS"

PUBKEY_CONTENT="$(cat "${KEY_PATH}.pub")"
if ! grep -qxF "$PUBKEY_CONTENT" "$AUTHORIZED_KEYS"; then
  printf '%s\n' "$PUBKEY_CONTENT" >>"$AUTHORIZED_KEYS"
  chown "$HOST_USER:$HOST_USER" "$AUTHORIZED_KEYS"
fi

chmod 700 "$PROJECT_DIR/raspberry_tools"
chmod 755 "$MONITOR_SCRIPT"

cat >"$SUDOERS_FILE" <<EOF
${HOST_USER} ALL=(root) NOPASSWD: ${MONITOR_SCRIPT} *
EOF
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" >/dev/null

sudo -u "$HOST_USER" bash -lc "ssh -i '$KEY_PATH' -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${HOST_USER}@127.0.0.1 'true'" >/dev/null 2>&1 || true

printf 'monitor_control_installed=yes\n'
printf 'ssh_key=%s\n' "$KEY_PATH"
printf 'sudoers=%s\n' "$SUDOERS_FILE"
