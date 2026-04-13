#!/usr/bin/env bash
set -euo pipefail

DEFAULT_IFACE="${WIFI_IFACE:-wlan0}"

usage() {
  cat <<'EOF'
Usage:
  wifi-manager list [iface]
  wifi-manager choose [iface]
  wifi-manager connect <ssid> [password] [iface]
  wifi-manager status [iface]
  wifi-manager tui

Commands:
  list      Rescan and list visible Wi-Fi networks.
  choose    Show a numbered list of visible networks and connect to one.
  connect   Connect directly to the given SSID. If password is omitted, prompt.
  status    Show the current Wi-Fi connection, IPv4 address, and default route.
  tui       Open NetworkManager's text UI (nmtui).

Examples:
  sudo wifi-manager list
  sudo wifi-manager choose
  sudo wifi-manager connect "YourWifiName"
  sudo wifi-manager connect "YourWifiName" "super-secret-password"
  wifi-manager status
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

ensure_root_for_mutation() {
  local action="$1"
  shift || true

  case "$action" in
    choose|connect|tui)
      if (( EUID != 0 )); then
        exec sudo --preserve-env=WIFI_IFACE "$0" "$action" "$@"
      fi
      ;;
  esac
}

wifi_scan_raw() {
  local iface="$1"

  nmcli --colors no --terse --fields IN-USE,SSID,SIGNAL,SECURITY \
    device wifi list ifname "$iface" --rescan yes
}

list_networks() {
  local iface="$1"

  wifi_scan_raw "$iface" \
    | awk -F: '
        length($2) && !seen[$2]++ {
          current = ($1 == "*") ? "*" : " "
          security = ($4 == "" ? "open" : $4)
          printf "%s | %-32s | %3s%% | %s\n", current, $2, $3, security
        }
      '
}

current_security_for_ssid() {
  local iface="$1"
  local ssid="$2"

  wifi_scan_raw "$iface" \
    | awk -F: -v target="$ssid" '
        $2 == target {
          print $4
          exit
        }
      '
}

show_status() {
  local iface="$1"
  local connection ipv4 gateway

  connection="$(nmcli --colors no --terse --fields GENERAL.CONNECTION device show "$iface" 2>/dev/null | cut -d: -f2- || true)"
  ipv4="$(ip -4 -o addr show "$iface" | awk '{print $4}' | head -n 1 || true)"
  gateway="$(ip route | awk -v iface="$iface" '$1 == "default" && $5 == iface { print $3; exit }')"

  printf 'Interface: %s\n' "$iface"
  printf 'Connection: %s\n' "${connection:-disconnected}"
  printf 'IPv4: %s\n' "${ipv4:-none}"
  printf 'Gateway: %s\n' "${gateway:-none}"
}

connect_network() {
  local iface="$1"
  local ssid="$2"
  local password="${3:-}"
  local security
  local -a cmd

  security="$(current_security_for_ssid "$iface" "$ssid")"
  if [[ -z "$password" && -n "$security" && "$security" != "--" ]]; then
    read -r -s -p "Password for ${ssid}: " password
    printf '\n'
  fi

  cmd=(nmcli device wifi connect "$ssid" ifname "$iface")
  if [[ -n "$password" ]]; then
    cmd+=(password "$password")
  fi

  "${cmd[@]}"
  printf '\n'
  show_status "$iface"
}

choose_network() {
  local iface="$1"
  local selection ssid password security
  local entries=()
  local labels=()

  while IFS= read -r line; do
    entries+=("$line")
  done < <(
    wifi_scan_raw "$iface" \
      | awk -F: '
          length($2) && !seen[$2]++ {
            current = ($1 == "*") ? " [current]" : ""
            security = ($4 == "" ? "open" : $4)
            printf "%s|%s%%%s|%s\n", $2, $3, current, security
          }
        '
  )

  ((${#entries[@]} > 0)) || die "no Wi-Fi networks found on ${iface}"

  printf 'Visible Wi-Fi networks on %s:\n' "$iface"
  local i=1
  for entry in "${entries[@]}"; do
    IFS='|' read -r ssid selection security <<<"$entry"
    printf '  %2d) %-32s signal=%-10s security=%s\n' "$i" "$ssid" "$selection" "$security"
    labels+=("$ssid")
    ((i++))
  done
  printf '  %2d) Enter SSID manually\n' "$i"

  read -r -p "Choose a network number: " selection
  [[ "$selection" =~ ^[0-9]+$ ]] || die "please enter a number"

  if (( selection == i )); then
    read -r -p "SSID: " ssid
  elif (( selection >= 1 && selection < i )); then
    ssid="${labels[selection-1]}"
  else
    die "invalid selection"
  fi

  security="$(current_security_for_ssid "$iface" "$ssid")"
  password=""
  if [[ -n "$security" && "$security" != "--" ]]; then
    read -r -s -p "Password for ${ssid}: " password
    printf '\n'
  fi

  connect_network "$iface" "$ssid" "$password"
}

main() {
  local action="${1:-}"

  [[ -n "$action" ]] || {
    usage
    exit 1
  }

  ensure_root_for_mutation "$@"
  require_command nmcli

  case "$action" in
    list)
      list_networks "${2:-$DEFAULT_IFACE}"
      ;;
    choose)
      choose_network "${2:-$DEFAULT_IFACE}"
      ;;
    connect)
      [[ -n "${2:-}" ]] || die "missing SSID"
      connect_network "${4:-$DEFAULT_IFACE}" "$2" "${3:-}"
      ;;
    status)
      show_status "${2:-$DEFAULT_IFACE}"
      ;;
    tui)
      exec nmtui
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      die "unknown command: ${action}"
      ;;
  esac
}

main "$@"
