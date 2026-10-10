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
    # "restart", not "start": the sudoers rule from deploy.sh allows restart
    # without a password, so this works even if the ssh session has dropped.
    sudo -n systemctl restart demoneye 2>/dev/null || sudo systemctl restart demoneye ||
      echo "!! could not restart the eye: sudo systemctl restart demoneye"
  fi
}
trap restart EXIT

# Same display settings as the installed demoneye.service (each Pi sets its
# own KMSDRM index), falling back to 1 (the HDMI card on a Pi 5).
unit_env=$(systemctl show demoneye -p Environment --value 2>/dev/null || true)
unit_idx=$(echo "$unit_env" | tr ' ' '\n' | sed -n 's/^SDL_KMSDRM_DEVICE_INDEX=//p' | tail -1)
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-kmsdrm}"
export SDL_KMSDRM_DEVICE_INDEX="${SDL_KMSDRM_DEVICE_INDEX:-${unit_idx:-1}}"

PY=./venv/bin/python; [ -x "$PY" ] || PY=python3
status=0
"$PY" test_pattern.py "$@" || status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then   # 130 = Ctrl+C: the normal way out
  echo "!! the test pattern failed (exit $status) — see the error above"
  exit "$status"
fi
