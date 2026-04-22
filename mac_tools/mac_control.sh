#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"

if [[ -z "$action" ]]; then
  echo "usage: mac_control.sh <open_youtube|open_spotify|open_chatgpt|open_safari>" >&2
  exit 1
fi

open_url() {
  local url="$1"
  open "$url"
  printf 'action=%s url=%s\n' "$action" "$url"
}

case "$action" in
  open_youtube)
    open_url "https://www.youtube.com/"
    ;;
  open_spotify)
    if open -a "Spotify" >/dev/null 2>&1; then
      printf 'action=%s app=Spotify\n' "$action"
    else
      open_url "https://open.spotify.com/"
    fi
    ;;
  open_chatgpt)
    open_url "https://chatgpt.com/"
    ;;
  open_safari)
    open -a "Safari"
    printf 'action=%s app=Safari\n' "$action"
    ;;
  *)
    echo "unsupported action: $action" >&2
    exit 1
    ;;
esac
