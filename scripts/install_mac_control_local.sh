#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${MAC_CONTROL_INSTALL_ROOT:-${HOME}/ha-command-bridge/mac_tools}"
TARGET_SCRIPT="${INSTALL_ROOT}/mac_control.sh"
AUTHORIZED_KEYS_FILE="${HOME}/.ssh/authorized_keys"

if [[ $# -gt 1 ]]; then
  echo "Usage: ./scripts/install_mac_control_local.sh [<pubkey-file>]" >&2
  echo "You can also pipe the public key on stdin." >&2
  exit 1
fi

if [[ $# -eq 1 ]]; then
  public_key="$(cat "$1")"
else
  if [[ -t 0 ]]; then
    echo "Missing public key. Pass a file or pipe the key on stdin." >&2
    exit 1
  fi
  public_key="$(cat)"
fi

public_key="$(printf '%s' "$public_key" | tail -n 1 | tr -d '\r')"
if [[ -z "$public_key" ]]; then
  echo "Missing public key. Pass a file or pipe the key on stdin." >&2
  exit 1
fi

mkdir -p "${INSTALL_ROOT}" "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
cp "${PROJECT_DIR}/mac_tools/mac_control.sh" "${TARGET_SCRIPT}"
chmod 755 "${TARGET_SCRIPT}"
touch "${AUTHORIZED_KEYS_FILE}"
chmod 600 "${AUTHORIZED_KEYS_FILE}"

authorized_key_added=no
if ! grep -qxF "$public_key" "${AUTHORIZED_KEYS_FILE}"; then
  printf '%s\n' "$public_key" >> "${AUTHORIZED_KEYS_FILE}"
  authorized_key_added=yes
fi

printf 'mac_control_script=%s\n' "${TARGET_SCRIPT}"
printf 'authorized_key_added=%s\n' "${authorized_key_added}"
printf 'authorized_keys=%s\n' "${AUTHORIZED_KEYS_FILE}"
printf '%s\n' 'Ensure Remote Login is enabled on this Mac so the Raspberry Pi can connect over SSH.'
