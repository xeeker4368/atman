#!/usr/bin/env bash
#
# Start Project Anam.
#
# Local-only by default. --lan binds the backend to all interfaces for trusted
# household LAN/VPN use.

set -euo pipefail
set -m

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PID=""
CLEANED_UP=false
LAN_MODE=false

BACKEND_PORT="${ANAM_API_PORT:-8000}"

usage() {
  cat <<EOF
Usage: ./start.sh [options]

Options:
  --lan     Bind the backend to 0.0.0.0 so household devices can reach it.
            For trusted LAN/VPN use only — never expose this to the internet.
  --help    Show this help.

Environment:
  ANAM_API_PORT   Backend port, default 8000.

There is no frontend to start yet; it arrives in a later phase. When it does,
its dev server must stay bound to 127.0.0.1 even under --lan. This is
load-bearing, not a convenience: the dev server proxies /api to the backend on
loopback, so every request arriving through it looks local to the backend. The
admin surface is gated on the peer address being loopback, so exposing the dev
server on the LAN would hand every LAN client a loopback-looking path straight
through that gate. LAN clients use the backend port directly.
EOF
}

is_process_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

group_has_processes() {
  # True if any process other than this shell remains in the process group.
  local pgid="$1" exclude="$2"
  [ -z "$pgid" ] && return 1
  local remaining
  remaining="$(pgrep -g "$pgid" 2>/dev/null | grep -v "^${exclude}$" | grep -v "^$$\$" || true)"
  [ -n "$remaining" ]
}

stop_process_tree() {
  local label="$1" root_pid="$2"
  [ -z "$root_pid" ] && return 0

  if ! is_process_alive "$root_pid"; then
    echo "$label (PID $root_pid) already stopped."
    return 0
  fi

  echo "Stopping $label (PID $root_pid)..."
  local pgid
  pgid="$(ps -o pgid= -p "$root_pid" 2>/dev/null | tr -d '[:space:]' || true)"

  if [ -n "$pgid" ]; then
    kill -TERM "-$pgid" 2>/dev/null || true
  else
    kill -TERM "$root_pid" 2>/dev/null || true
  fi

  # Wait for the whole process group to drain, not just the root PID. The
  # backend's own graceful shutdown outlives the subshell that launched it, so
  # returning when the root exits would print "Stopped." while the server is
  # still shutting down — claiming a completion that hasn't happened yet.
  for _ in $(seq 1 50); do
    if ! is_process_alive "$root_pid" && ! group_has_processes "$pgid" "$root_pid"; then
      wait "$root_pid" 2>/dev/null || true
      return 0
    fi
    sleep 0.2
  done

  echo "$label did not stop after TERM; sending KILL."
  if [ -n "$pgid" ]; then
    kill -KILL "-$pgid" 2>/dev/null || true
  else
    kill -KILL "$root_pid" 2>/dev/null || true
  fi
  wait "$root_pid" 2>/dev/null || true
}

cleanup() {
  [ "$CLEANED_UP" = true ] && return 0
  CLEANED_UP=true
  trap - INT TERM
  echo ""
  echo "Stopping Project Anam..."
  stop_process_tree "backend" "$BACKEND_PID"
  echo "Stopped."
}

handle_signal() {
  cleanup
  exit 130
}

wait_for_url() {
  local url="$1" label="$2" attempts="${3:-30}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$label is ready: $url"
      return 0
    fi
    sleep 1
  done
  echo "Warning: $label did not become ready at $url"
  return 1
}

python_for_backend() {
  if [ -x "$ROOT_DIR/venv/bin/python" ]; then
    echo "$ROOT_DIR/venv/bin/python"
  elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    echo "$ROOT_DIR/.venv/bin/python"
  else
    echo "python3"
  fi
}

detect_lan_urls() {
  local port="$1" found=false
  if command -v ipconfig >/dev/null 2>&1; then
    for iface in en0 en1 en2 bridge0; do
      local ip
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      if [ -n "$ip" ]; then
        echo "  http://${ip}:${port}"
        found=true
      fi
    done
  fi
  [ "$found" = false ] && echo "  Could not detect a LAN IPv4 address."
  return 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --lan)  LAN_MODE=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1"; echo ""; usage; exit 2 ;;
  esac
  shift
done

trap cleanup EXIT
trap handle_signal INT TERM

cd "$ROOT_DIR"

BIND_HOST="127.0.0.1"
if [ "$LAN_MODE" = true ]; then
  BIND_HOST="0.0.0.0"
  echo "LAN mode: trusted household LAN/VPN only. Do not expose to the internet."
fi

echo "Starting Project Anam backend on ${BIND_HOST}:${BACKEND_PORT}..."
(
  cd "$ROOT_DIR"
  ANAM_API_HOST="$BIND_HOST" ANAM_API_PORT="$BACKEND_PORT" \
    "$(python_for_backend)" run_server.py
) &
BACKEND_PID=$!

wait_for_url "http://127.0.0.1:${BACKEND_PORT}/api/health" "Backend" 30 || true

echo ""
echo "Backend PID: $BACKEND_PID"
echo "Local URL:   http://127.0.0.1:${BACKEND_PORT}"
if [ "$LAN_MODE" = true ]; then
  echo ""
  echo "LAN URL(s) — household devices use these:"
  detect_lan_urls "$BACKEND_PORT"
fi
echo ""
echo "Press Ctrl+C to stop."
echo ""

while true; do
  sleep 2
  if ! is_process_alive "$BACKEND_PID"; then
    echo "Backend stopped unexpectedly."
    exit 1
  fi
done
