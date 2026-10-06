#!/usr/bin/env python3
"""
Projector IR control.

Wraps `ir-ctl`, which hands the NEC encoding and 38kHz carrier to the kernel's
IR driver — far more timing-reliable than bit-banging GPIO from Python.

Closed loop: the projector's HDMI hot-plug line is the feedback. A projector
that's off stops announcing itself, so /sys/class/drm/card*-HDMI-A-*/status
reads "disconnected"; on, it reads "connected". With `verify = true` (the
default) every power command checks that state first — no press if it's
already where we want it, which matters because POWER is a TOGGLE — then
presses, waits for the state to change, and retries once if it didn't. A missed
press, or a Pi that restarted while the projector was already on, can no
longer leave the projector in the opposite state from the show.

(A forced video mode — video=HDMI-A-1:...D in cmdline.txt — makes the status
always read "connected", which blinds this. It's detected, and the code falls
back to blind presses with a warning.)

Standalone:
    python ir_control.py status        # is the projector on? (HDMI state)
    python ir_control.py on
    python ir_control.py off
    python ir_control.py send KEY_POWER
    python ir_control.py test          # on, wait, off

From the eye app: demoneye.py calls power_on()/power_off() when Home Assistant
turns the show on and off (see [projector] in config.ini).
"""

import configparser
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time

# --- defaults; demoneye.py overrides these from config.ini [projector] ---
KEYMAP = "/home/fpp/groview.toml"
DEVICE = ""             # e.g. /dev/lirc0; blank lets ir-ctl choose
POWER_KEY = "KEY_POWER"
SEPARATE_ON_OFF = False  # True if your remote has discrete on/off keys
ON_KEY = "KEY_POWER"
OFF_KEY = "KEY_POWER"
REPEAT = 1               # send the code N times (some projectors miss the first)
REPEAT_GAP = 0.25        # seconds between repeats
OFF_CONFIRM = 0          # many projectors need POWER twice to confirm shutdown
OFF_CONFIRM_GAP = 1.5    # seconds before the confirming press
WARMUP = 0.0             # seconds to wait after power-on before showing the eye

# --- closed-loop verification via the HDMI connector status ---
VERIFY = True            # check HDMI state before/after every power command
STATUS_PATH = "auto"     # "auto" = any HDMI connector, or e.g.
                         # /sys/class/drm/card1-HDMI-A-1/status
ON_TIMEOUT = 45.0        # seconds to wait for HDMI to appear after power-on
OFF_TIMEOUT = 60.0       # seconds to wait for HDMI to drop after power-off
RETRIES = 1              # extra attempts if the state didn't change

_LOCK = threading.Lock()       # an "off" arriving mid-"on" waits its turn
_warned_forced = False


def load_config(path=None):
    """Standalone use: read [projector] from config.ini so the command line
    behaves exactly like the running app."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if not os.path.exists(path):
        print(f"[ir] {path} not found — using built-in defaults", flush=True)
        return
    cp = configparser.ConfigParser()
    cp.read(path)
    if cp.has_section("projector"):
        configure(cp["projector"])
        print(f"[ir] loaded {path}", flush=True)


def configure(sec):
    """Apply a config.ini [projector] section (a ConfigParser section proxy)."""
    global KEYMAP, DEVICE, POWER_KEY, SEPARATE_ON_OFF, ON_KEY, OFF_KEY
    global REPEAT, REPEAT_GAP, OFF_CONFIRM, OFF_CONFIRM_GAP, WARMUP
    KEYMAP = sec.get("keymap", KEYMAP)
    DEVICE = sec.get("device", DEVICE)
    POWER_KEY = sec.get("power_key", POWER_KEY)
    SEPARATE_ON_OFF = sec.getboolean("separate_on_off", SEPARATE_ON_OFF)
    ON_KEY = sec.get("on_key", ON_KEY)
    OFF_KEY = sec.get("off_key", OFF_KEY)
    REPEAT = sec.getint("repeat", REPEAT)
    REPEAT_GAP = sec.getfloat("repeat_gap_s", REPEAT_GAP)
    OFF_CONFIRM = sec.getint("off_confirm", OFF_CONFIRM)
    OFF_CONFIRM_GAP = sec.getfloat("off_confirm_gap_s", OFF_CONFIRM_GAP)
    WARMUP = sec.getfloat("warmup_s", WARMUP)
    global VERIFY, STATUS_PATH, ON_TIMEOUT, OFF_TIMEOUT, RETRIES
    VERIFY = sec.getboolean("verify", VERIFY)
    STATUS_PATH = sec.get("status_path", STATUS_PATH).strip() or "auto"
    ON_TIMEOUT = sec.getfloat("on_timeout_s", ON_TIMEOUT)
    OFF_TIMEOUT = sec.getfloat("off_timeout_s", OFF_TIMEOUT)
    RETRIES = sec.getint("retries", RETRIES)
    st = projector_state()
    fb = ("off (verify = false)" if not VERIFY else
          "unavailable — blind presses" if st is None else
          f"HDMI says projector is {'ON' if st else 'OFF'}")
    print(f"[ir] projector control enabled (ir-ctl {'found' if available() else 'NOT FOUND'}; "
          f"feedback {fb})", flush=True)


def available():
    """True if ir-ctl is installed."""
    return shutil.which("ir-ctl") is not None


def send_key(key, keymap=None, device=None, repeat=None, gap=None):
    """Send one key via ir-ctl. Returns True on success (never raises)."""
    if not available():
        print("[ir] ir-ctl not found (apt install ir-ctl / v4l-utils)", flush=True)
        return False
    cmd = ["ir-ctl"]
    if device or DEVICE:
        cmd += ["-d", device or DEVICE]
    cmd += ["-K", key, "-k", keymap or KEYMAP]
    n = REPEAT if repeat is None else repeat
    g = REPEAT_GAP if gap is None else gap
    ok = True
    for i in range(max(1, n)):
        if i:
            time.sleep(g)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                ok = False
                print(f"[ir] {key} failed: {(r.stderr or r.stdout).strip()[:160]}", flush=True)
        except subprocess.TimeoutExpired:
            ok = False
            print(f"[ir] {key} timed out", flush=True)
        except Exception as e:
            ok = False
            print(f"[ir] {key} error: {e}", flush=True)
    if ok:
        print(f"[ir] sent {key}", flush=True)
    return ok


# ---------------------------------------------------------------- feedback
def _forced_mode():
    """True if the kernel is forcing an HDMI port on (video=HDMI-A-n:...D),
    which makes its status read "connected" whatever the projector is doing."""
    try:
        with open("/proc/cmdline") as f:
            return bool(re.search(r"video=HDMI-A-\d+:\S*?D\b", f.read()))
    except OSError:
        return False


def projector_state():
    """True = projector on (HDMI connected), False = off, None = can't tell."""
    global _warned_forced
    if not VERIFY:
        return None
    if _forced_mode():
        if not _warned_forced:
            print("[ir] video=...D in cmdline.txt forces HDMI 'connected', so the projector's "
                  "real state can't be seen — using blind presses. Remove it to enable "
                  "verification.", flush=True)
            _warned_forced = True
        return None
    paths = ([STATUS_PATH] if STATUS_PATH != "auto"
             else glob.glob("/sys/class/drm/card*-HDMI-A-*/status"))
    states = []
    for p in paths:
        try:
            with open(p) as f:
                states.append(f.read().strip() == "connected")
        except OSError:
            pass
    if not states:
        return None
    return any(states)


def _wait_for(want, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if projector_state() == want:
            return time.time() - t0
        time.sleep(0.5)
    return None


def _press_on():
    key = ON_KEY if SEPARATE_ON_OFF else POWER_KEY
    return send_key(key)


def _press_off():
    key = OFF_KEY if SEPARATE_ON_OFF else POWER_KEY
    ok = send_key(key)
    for _ in range(max(0, OFF_CONFIRM)):
        print(f"[ir] waiting {OFF_CONFIRM_GAP:.1f}s to confirm shutdown", flush=True)
        time.sleep(OFF_CONFIRM_GAP)
        ok = send_key(key) and ok
    return ok


def _drive(want):
    """Get the projector to `want` (True on / False off), verifying via HDMI."""
    word = "on" if want else "off"
    press = _press_on if want else _press_off
    timeout = ON_TIMEOUT if want else OFF_TIMEOUT
    with _LOCK:
        st = projector_state()
        if st is None:                               # no feedback: old blind behaviour
            ok = press()
            if ok and want and WARMUP > 0:
                print(f"[ir] warming up {WARMUP:.0f}s", flush=True)
                time.sleep(WARMUP)
            return ok
        if st == want:
            print(f"[ir] projector already {word} — not pressing (POWER is a toggle)", flush=True)
            return True
        for attempt in range(1 + max(0, RETRIES)):
            if attempt:
                # re-check right before a retry: a slow projector may have made
                # it after all, and a blind second toggle would undo it
                if projector_state() == want:
                    print(f"[ir] projector {word} (late)", flush=True)
                    return True
                print(f"[ir] projector didn't turn {word} within {timeout:.0f}s — "
                      f"retrying ({attempt}/{RETRIES})", flush=True)
            press()
            took = _wait_for(want, timeout)
            if took is not None:
                print(f"[ir] projector {word} — confirmed by HDMI after {took:.1f}s", flush=True)
                if want and WARMUP > 0:
                    print(f"[ir] warming up {WARMUP:.0f}s", flush=True)
                    time.sleep(WARMUP)
                return True
        print(f"[ir] GAVE UP: projector still {'off' if want else 'on'} after {1 + RETRIES} "
              f"attempt(s). Check the IR transmitter's aim and the projector's power.", flush=True)
        return False


def power_on():
    """Turn the projector on, confirming via HDMI when possible."""
    return _drive(True)


def power_off():
    """Turn the projector off, confirming via HDMI when possible."""
    return _drive(False)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    load_config()
    cmd = sys.argv[1].lower()
    if cmd == "status":
        st = projector_state()
        print("projector is ON (HDMI connected)" if st else
              "projector is OFF (HDMI disconnected)" if st is False else
              "can't tell (no HDMI status, verify off, or a forced video mode)")
        sys.exit(0 if st is not None else 2)
    if cmd == "on":
        power_on()
    elif cmd == "off":
        power_off()
    elif cmd == "send" and len(sys.argv) > 2:
        send_key(sys.argv[2])
    elif cmd == "test":
        print("powering on..."); power_on()
        time.sleep(8)
        print("powering off..."); power_off()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
