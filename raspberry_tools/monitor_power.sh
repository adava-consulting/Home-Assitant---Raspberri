#!/usr/bin/env bash
set -euo pipefail

TTY_DEVICE="${MONITOR_TTY_DEVICE:-/dev/tty1}"

usage() {
  cat <<'EOF'
Usage:
  monitor_power.sh <on|off|status>

Commands:
  on      Wake or unblank the Raspberry Pi HDMI display.
  off     Put the Raspberry Pi HDMI display into standby/blank mode.
  status  Print which monitor-control methods are available.
EOF
}

log() {
  printf '%s\n' "$*"
}

have_command() {
  command -v "$1" >/dev/null 2>&1
}

try_vcgencmd() {
  local power="$1"
  have_command vcgencmd || return 1
  vcgencmd display_power "$power" >/dev/null
}

try_tvservice_off() {
  have_command tvservice || return 1
  tvservice -o >/dev/null
}

try_tvservice_on() {
  have_command tvservice || return 1
  tvservice -p >/dev/null
  if have_command fbset; then
    fbset -depth 8 >/dev/null 2>&1 || true
    fbset -depth 16 >/dev/null 2>&1 || true
  fi
}

try_sysfs_blank() {
  local value="$1"
  local changed=1
  local node
  for node in /sys/class/graphics/fb0/blank /sys/class/graphics/fb1/blank; do
    [[ -e "$node" ]] || continue
    printf '%s\n' "$value" >"$node"
    changed=0
  done
  return "$changed"
}

try_setterm_off() {
  have_command setterm || return 1
  [[ -e "$TTY_DEVICE" ]] || return 1
  chvt 1 >/dev/null 2>&1 || true
  setterm --blank force --powersave powerdown --term linux >"$TTY_DEVICE" <"$TTY_DEVICE"
}

try_setterm_on() {
  have_command setterm || return 1
  [[ -e "$TTY_DEVICE" ]] || return 1
  chvt 1 >/dev/null 2>&1 || true
  setterm --blank poke --term linux >"$TTY_DEVICE" <"$TTY_DEVICE"
  printf '\n' >"$TTY_DEVICE"
}

show_status() {
  log "tty_device=${TTY_DEVICE}"
  log "vcgencmd=$([[ $(have_command vcgencmd; echo $?) -eq 0 ]] && echo yes || echo no)"
  log "tvservice=$([[ $(have_command tvservice; echo $?) -eq 0 ]] && echo yes || echo no)"
  log "fbset=$([[ $(have_command fbset; echo $?) -eq 0 ]] && echo yes || echo no)"
  log "setterm=$([[ $(have_command setterm; echo $?) -eq 0 ]] && echo yes || echo no)"
  log "sysfs_fb0=$([[ -e /sys/class/graphics/fb0/blank ]] && echo yes || echo no)"
  log "sysfs_fb1=$([[ -e /sys/class/graphics/fb1/blank ]] && echo yes || echo no)"
  log "tty_exists=$([[ -e ${TTY_DEVICE} ]] && echo yes || echo no)"
}

power_off() {
  if try_setterm_off; then
    log "method=setterm action=off"
    return 0
  fi
  if try_vcgencmd 0; then
    log "method=vcgencmd action=off"
    return 0
  fi
  if try_tvservice_off; then
    log "method=tvservice action=off"
    return 0
  fi
  if try_sysfs_blank 1; then
    log "method=sysfs action=off"
    return 0
  fi
  return 1
}

power_on() {
  if try_setterm_on; then
    log "method=setterm action=on"
    return 0
  fi
  if try_vcgencmd 1; then
    log "method=vcgencmd action=on"
    return 0
  fi
  if try_tvservice_on; then
    log "method=tvservice action=on"
    return 0
  fi
  if try_sysfs_blank 0; then
    log "method=sysfs action=on"
    return 0
  fi
  return 1
}

main() {
  local command="${1:-}"
  case "$command" in
    on)
      power_on || {
        printf 'error: no supported wake method succeeded\n' >&2
        exit 1
      }
      ;;
    off)
      power_off || {
        printf 'error: no supported sleep method succeeded\n' >&2
        exit 1
      }
      ;;
    status)
      show_status
      ;;
    -h|--help|help|"")
      usage
      [[ -n "$command" ]] || exit 1
      ;;
    *)
      printf 'error: unknown command: %s\n' "$command" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
