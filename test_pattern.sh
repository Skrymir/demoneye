#!/usr/bin/env bash
# Show the projector alignment test pattern on this Pi's projector.
#
#   ./test_pattern.sh                  # white pattern, until Ctrl+C
#   ./test_pattern.sh --color red      # any test_pattern.py option works
#
# From the Mac:  ssh -t joshc@pi-eye-left.home demoneye/test_pattern.sh
#
# The eye service holds the display, so this stops it first and starts it
# again when you quit (Ctrl+C), even if the pattern fails.
set -euo pipefail
cd "$(dirname "$0")"

was_running=0
if systemctl is-active --quiet demoneye; then
  was_running=1
  echo "==> stopping the eye service (it holds the display)"
  sudo systemctl stop demoneye
fi
restart() {
  if [ "$was_running" = 1 ]; then
    echo "==> starting the eye service again"
    sudo systemctl start demoneye || echo "!! could not restart: sudo systemctl start demoneye"
  fi
}
trap restart EXIT

# Same display settings as demoneye.service (straight to HDMI from the console).
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-kmsdrm}"
export SDL_KMSDRM_DEVICE_INDEX="${SDL_KMSDRM_DEVICE_INDEX:-1}"

PY=./venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" test_pattern.py "$@" || true
