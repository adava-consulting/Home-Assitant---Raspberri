#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
BRIDGE_DATA_DIR="${BRIDGE_DATA_DIR:-/home/claude-host-home/ha-command-bridge-data}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
BACKUP_INCLUDE_AUDIO_CACHE="${BACKUP_INCLUDE_AUDIO_CACHE:-0}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_PATH="${BACKUP_DIR}/ha-command-bridge-backup-${TIMESTAMP}.tar.gz"
STAGING_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${STAGING_DIR}"
}

trap cleanup EXIT

mkdir -p "${BACKUP_DIR}"

copy_if_exists() {
  local source_path="$1"
  local destination_path="$2"

  if [[ -e "${source_path}" ]]; then
    mkdir -p "$(dirname "${destination_path}")"
    cp -a "${source_path}" "${destination_path}"
  fi
}

copy_if_exists "${PROJECT_DIR}/.env" "${STAGING_DIR}/project/.env"
copy_if_exists "${PROJECT_DIR}/voice_model.json" "${STAGING_DIR}/project/voice_model.json"
copy_if_exists "${PROJECT_DIR}/credentials.env" "${STAGING_DIR}/project/credentials.env"
copy_if_exists "${PROJECT_DIR}/voice_services/.env" "${STAGING_DIR}/project/voice_services.env"

if [[ -d "${BRIDGE_DATA_DIR}" ]]; then
  if [[ "${BACKUP_INCLUDE_AUDIO_CACHE}" == "1" ]]; then
    copy_if_exists "${BRIDGE_DATA_DIR}" "${STAGING_DIR}/data/ha-command-bridge-data"
  else
    mkdir -p "${STAGING_DIR}/data/ha-command-bridge-data"
    while IFS= read -r entry; do
      copy_if_exists "${entry}" "${STAGING_DIR}/data/ha-command-bridge-data/$(basename "${entry}")"
    done < <(find "${BRIDGE_DATA_DIR}" -mindepth 1 -maxdepth 1 ! -name 'audio-cache')
  fi
fi

tar -czf "${ARCHIVE_PATH}" -C "${STAGING_DIR}" .

printf 'Backup created: %s\n' "${ARCHIVE_PATH}"
