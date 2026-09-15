#!/usr/bin/env python3
"""
Projector IR control.

Wraps `ir-ctl`, which hands the NEC encoding and 38kHz carrier to the kernel's
IR driver — far more timing-reliable than bit-banging GPIO from Python.

Standalone:
    python ir_control.py on
    python ir_control.py off
    python ir_control.py send KEY_POWER
    python ir_control.py test          # on, wait, off

From the eye app: demoneye.py calls power_on()/power_off() when Home Assistant
turns the show on and off (see [projector] in config.ini).
"""

import configparser
import os
import shutil
import subprocess
import sys
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


def load_config(path=None):
    """Load [projector] settings from config.ini so the command line behaves
    exactly like the running app. Called automatically when run standalone."""
    global KEYMAP, DEVICE, POWER_KEY, SEPARATE_ON_OFF, ON_KEY, OFF_KEY
    global REPEAT, REPEAT_GAP, OFF_CONFIRM, OFF_CONFIRM_GAP, WARMUP
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if not os.path.exists(path):
        print(f"[ir] {path} not found — using built-in defaults", flush=True)
        return
    cp = configparser.ConfigParser()
    cp.read(path)
    if not cp.has_section("projector"):
        return
    g = cp["projector"]
    KEYMAP = g.get("keymap", KEYMAP)
    DEVICE = g.get("device", DEVICE)
    POWER_KEY = g.get("power_key", POWER_KEY)
    SEPARATE_ON_OFF = g.getboolean("separate_on_off", SEPARATE_ON_OFF)
    ON_KEY = g.get("on_key", ON_KEY)
    OFF_KEY = g.get("off_key", OFF_KEY)
    REPEAT = g.getint("repeat", REPEAT)
    REPEAT_GAP = g.getfloat("repeat_gap_s", REPEAT_GAP)
    OFF_CONFIRM = g.getint("off_confirm", OFF_CONFIRM)
    OFF_CONFIRM_GAP = g.getfloat("off_confirm_gap_s", OFF_CONFIRM_GAP)
    WARMUP = g.getfloat("warmup_s", WARMUP)
    print(f"[ir] loaded {path}", flush=True)


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


def power_on():
    """Power the projector on. Waits WARMUP seconds if configured."""
    key = ON_KEY if SEPARATE_ON_OFF else POWER_KEY
    ok = send_key(key)
    if ok and WARMUP > 0:
        print(f"[ir] warming up {WARMUP:.0f}s", flush=True)
        time.sleep(WARMUP)
    return ok


def power_off():
    """Power the projector off, including a confirming press if configured."""
    key = OFF_KEY if SEPARATE_ON_OFF else POWER_KEY
    ok = send_key(key)
    for i in range(max(0, OFF_CONFIRM)):
        print(f"[ir] waiting {OFF_CONFIRM_GAP:.1f}s to confirm shutdown", flush=True)
        time.sleep(OFF_CONFIRM_GAP)
        ok = send_key(key) and ok
    return ok


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    load_config()
    cmd = sys.argv[1].lower()
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
