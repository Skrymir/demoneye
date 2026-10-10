#!/usr/bin/env bash
# Deploy the current code to every Pi in one go.
#
#   ./deploy.sh                      # deploy to the Pis listed in PIS below
#   ./deploy.sh pi-eye-left.home          # just one
#   ./deploy.sh --no-restart         # copy files only
#   ./deploy.sh --skip-tests         # don't run the test suite first
#
# What it does, per Pi:
#   1. copies the project with rsync (only changed files are sent)
#   2. NEVER touches that Pi's own config.ini / bridge.ini or its venv
#   3. installs any new Python requirements (the eyes' and the bridge's)
#   4. restarts the demoneye service and shows its first log lines
#   5. restarts the tracking bridge too, on whichever Pi runs it
#
# One-time setup on your Mac (so it doesn't ask for passwords):
#   ssh-copy-id joshc@pi-eye-left.home
#   ssh-copy-id joshc@pi-eye-right.home
# and on each Pi, let joshc restart the services without a password:
#   echo 'joshc ALL=(root) NOPASSWD: /usr/bin/systemctl restart demoneye, /usr/bin/systemctl restart ha-bridge' \
#     | sudo tee /etc/sudoers.d/demoneye && sudo chmod 440 /etc/sudoers.d/demoneye

set -euo pipefail

# ---- edit these to match your setup ----------------------------------------
PIS=(pi-eye-left.home pi-eye-right.home)   # hostnames or IPs
USER_NAME=joshc
REMOTE_DIR=demoneye              # relative to the user's home on the Pi
# ----------------------------------------------------------------------------

RESTART=1
RUN_TESTS=1
TARGETS=()
for arg in "$@"; do
  case "$arg" in
    --no-restart)                       RESTART=0 ;;
    --skip-tests|--skip-test|--no-tests) RUN_TESTS=0 ;;
    -h|--help)                          sed -n '2,22p' "$0"; exit 0 ;;
    -*)  echo "!! unknown option: $arg   (options: --skip-tests  --no-restart  --help)"; exit 2 ;;
    *)   TARGETS+=("$arg") ;;
  esac
done
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=("${PIS[@]}")

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Refuse to deploy an incomplete or out-of-date folder: rsync --delete would
# make the Pis match it, removing newer files there.
missing=()
for f in demoneye.py settings.py renderers.py directors.py glsl_common.py \
         glutil.py eye_shaders.py ir_control.py ha_status.py requirements.txt \
         requirements-bridge.txt tests; do
  [ -e "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "!! this folder is missing: ${missing[*]}"
  echo "   It looks like an older copy of the project — deploying it would replace the"
  echo "   newer code on the Pis. Update this folder to the current release first."
  exit 1
fi

# ---- 1. sanity: run the tests locally before shipping anything -------------
if [ "$RUN_TESTS" = 1 ]; then
  PY="./venv/bin/python"; [ -x "$PY" ] || PY="python3"
  if "$PY" -c "import pytest" 2>/dev/null; then
    echo "==> running tests"
    "$PY" -m pytest -q tests || { echo "!! tests failed — not deploying (use --skip-tests to override)"; exit 1; }
  else
    echo "==> pytest not installed locally; skipping tests"
  fi
fi

# Files that are per-machine or rebuilt on the Pi: never copied, never deleted.
EXCLUDES=(
  --exclude 'config.ini' --exclude 'bridge.ini'
  --exclude 'config.*.ini' --exclude 'bridge.*.ini' --exclude '*.local.ini'
  --exclude 'venv/' --exclude 'venv*/' --exclude '.venv/'
  --exclude '.git/' --exclude '__pycache__/' --exclude '.pytest_cache/'
  --exclude '*.pyc' --exclude '.DS_Store' --exclude '*.zip' --exclude '*.log'
  --exclude 'frames/' --exclude 'frames_*/'
  --exclude 'homeassistant/'
)

FAILED=()
for host in "${TARGETS[@]}"; do
  dest="${USER_NAME}@${host}"
  echo
  echo "==> ${host}"

  if ! sshout=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$dest" true 2>&1); then
    reason=$(echo "$sshout" | tail -1)
    echo "!! can't reach ${host} over ssh without a prompt: ${reason}"
    case "$sshout" in
      *"Host key verification failed"*|*"authenticity of host"*)
        echo "   first time using this name: run  ssh ${dest}  once and answer 'yes'" ;;
      *"Permission denied"*)
        echo "   your key isn't being offered: check the Host line in ~/.ssh/config covers ${host},"
        echo "   and  ssh-add --apple-use-keychain ~/.ssh/id_ed25519  (again after a Mac reboot)" ;;
      *"Could not resolve hostname"*)
        echo "   the name doesn't resolve: try  ping ${host}  or use its IP in PIS=(...)" ;;
      *"timed out"*|*"No route to host"*|*"Connection refused"*)
        echo "   the Pi isn't answering: is it powered on and on the network?" ;;
    esac
    FAILED+=("$host"); continue
  fi

  # 2. copy. --delete removes files you've deleted locally, but excluded files
  #    (each Pi's config.ini, its venv) are protected from deletion.
  if ! out=$(rsync -az --delete --itemize-changes "${EXCLUDES[@]}" ./ "${dest}:${REMOTE_DIR}/" 2>&1); then
    echo "$out" | sed 's/^/   /'
    echo "!! copy to ${host} failed"; FAILED+=("$host"); continue
  fi
  changed=$(echo "$out" | grep -v '^\.' || true)
  if [ -n "$changed" ]; then echo "$changed" | sed 's/^/   /'; else echo "   (no file changes)"; fi

  # create the venv on a fresh Pi, then install requirements (the bridge's
  # too: it runs on one of the Pis and deploy restarts it below). A failure
  # here must not abort the whole deploy: skip this Pi's restart, carry on.
  if ! ssh "$dest" "cd ${REMOTE_DIR} && \
      { [ -x venv/bin/python ] || python3 -m venv venv; } && \
      ./venv/bin/pip install -q -r requirements.txt -r requirements-bridge.txt"; then
    echo "!! installing requirements on ${host} failed — new code copied but NOT restarted"
    echo "   (no internet on the Pi? retry ./deploy.sh ${host} once it's back)"
    FAILED+=("$host"); continue
  fi
  ssh "$dest" "[ -f ${REMOTE_DIR}/config.ini ]" ||
    echo "   !! no config.ini on this Pi yet — copy one over and edit it"

  # 3. restart
  if [ "$RESTART" = 1 ]; then
    if ssh "$dest" "sudo -n systemctl restart demoneye" 2>/dev/null; then
      sleep 3
      ssh "$dest" "systemctl status demoneye --no-pager -n 6 | sed 's/^/   /'" || true
    else
      echo "   restart needs your password (or add the sudoers rule above):"
      ssh -t "$dest" "sudo systemctl restart demoneye" || FAILED+=("$host")
    fi
    # the tracking bridge, only where it's running (ONE Pi)
    if ssh "$dest" "systemctl is-active --quiet ha-bridge"; then
      if ssh "$dest" "sudo -n systemctl restart ha-bridge" 2>/dev/null; then
        echo "   tracking bridge restarted"
      else
        echo "   !! tracking bridge still runs the old code — add ha-bridge to the sudoers rule above,"
        echo "      or: ssh ${dest} sudo systemctl restart ha-bridge"
      fi
    fi
  fi
done

echo
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "==> deployed to: ${TARGETS[*]}"
else
  echo "!! problems with: ${FAILED[*]}"
  exit 1
fi
