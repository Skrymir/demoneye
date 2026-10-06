#!/usr/bin/env python3
"""
Check the config.ini on every Pi — each file on its own, and the pair against
each other — before something goes wrong on the house.

    python3 check_config.py                       # both Pis (PIS below)
    python3 check_config.py pi-eye-left.home           # just one
    python3 check_config.py --file a.ini b.ini    # check local copies, no ssh

Runs from your Mac over ssh (same setup as deploy.sh). Read-only: it never
changes anything on the Pis.

Per Pi it checks: every key is one the app actually reads (catches typos),
values have the right type and a sensible range, no inline comments (the app
does NOT strip them — `mirror = true ; note` breaks startup), the broker is
reachable from that Pi, IR settings point at a real ir-ctl and keymap, the
sprite atlas exists, and the installed service's role / mode / eye and the
SDL_KMSDRM_DEVICE_INDEX match the HDMI card the projector is actually on.

Tracking bridge (ha_bridge): whether it's installed / enabled / running on each
Pi, its bridge.ini (same key, type and inline-comment checks), that it has an HA
token, its Python deps, that Home Assistant is reachable, and that its broker
and topics match the eyes' config.ini on that Pi.

Across the pair: exactly one brain, same mode and eye, same broker and topics,
mirror on at most one Pi, stage window spans that don't overlap, and the
tracking bridge running on exactly ONE Pi.

    python3 check_config.py --file bridge.ini     # a local bridge.ini works too

Exit code 1 if anything is an error, so it can gate a deploy.
"""

import argparse
import configparser
import difflib
import re
import shlex
import subprocess
import sys
from urllib.parse import urlparse

# ---- edit to match deploy.sh -------------------------------------------------
PIS = ["pi-eye-left.home", "pi-eye-right.home"]
USER_NAME = "joshc"
REMOTE_DIR = "demoneye"            # relative to the user's home on the Pi
# ------------------------------------------------------------------------------

# Every key the app reads (settings.py + ir_control.py), with its type and an
# optional (low, high) range or set of allowed values.
F, I, B, S = "float", "int", "bool", "str"
SCHEMA = {
    "mqtt": {
        "host": (S, None), "port": (I, (1, 65535)), "username": (S, None),
        "password": (S, None), "topic": (S, None),
        "command_topic": (S, None), "target_topic": (S, None),
    },
    "control": {
        "start": (S, {"playing", "idle"}), "target_hold": (F, (0.1, 30)),
        "track_look_down": (F, (0.0, 1.0)), "stale_after_s": (F, (0.0, 60)),
        "display_retry_s": (F, (0.0, 120)),
    },
    "projector": {
        "ir_enabled": (B, None), "keymap": (S, None), "device": (S, None),
        "power_key": (S, None), "separate_on_off": (B, None),
        "on_key": (S, None), "off_key": (S, None), "repeat": (I, (1, 10)),
        "repeat_gap_s": (F, (0.0, 5)), "off_confirm": (I, (0, 5)),
        "off_confirm_gap_s": (F, (0.0, 10)), "warmup_s": (F, (0.0, 120)),
        "verify": (B, None), "status_path": (S, None),
        "on_timeout_s": (F, (5, 180)), "off_timeout_s": (F, (5, 180)), "retries": (I, (0, 3)),
    },
    "stage": {
        "stage_from": (F, (0.0, 1.0)), "stage_to": (F, (0.0, 1.0)),
        "figure_scale": (F, (0.05, 2.0)), "speed": (F, (0.001, 1.0)),
        "freeze_when_watched": (B, None), "figure_faces": (S, {"left", "right"}),
        "figure_stride": (F, (0.001, 1.0)), "figure_atlas": (S, None),
    },
    "display": {
        "center_x": (F, (0.0, 1.0)), "center_y": (F, (0.0, 1.0)),
        "scale": (F, (0.05, 5.0)), "mirror": (B, None), "mirror_gaze": (B, None),
        "gaze_scale": (F, (0.2, 4.0)), "gaze_scale_y": (F, (0.2, 4.0)),
    },
}
# Every key ha_bridge.py reads from bridge.ini.
BRIDGE_SCHEMA = {
    "home_assistant": {"url": (S, None), "token": (S, None)},
    "entities": {
        "target_count": (I, (1, 3)), "x_pattern": (S, None), "y_pattern": (S, None),
        "active_pattern": (S, None),
    },
    "mqtt": {
        "host": (S, None), "port": (I, (1, 65535)), "username": (S, None),
        "password": (S, None), "target_topic": (S, None), "command_topic": (S, None),
        "follow_command": (B, None), "start_enabled": (B, None),
    },
    "calibration": {
        "x_range_mm": (F, (50, 8000)), "x_offset_mm": (F, (-4000, 4000)), "invert_x": (B, None),
        "y_from_distance": (B, None), "y_near_mm": (F, (0, 8000)), "y_far_mm": (F, (0, 8000)),
        "smooth_factor": (F, (0.01, 1.0)), "publish_hz": (F, (1, 60)),
        "heartbeat_s": (F, (0.1, 10)), "hold_s": (F, (0.1, 30)),
    },
}
BRIDGE_DEFAULTS = {"follow_command": True, "start_enabled": False, "heartbeat_s": 0.75,
                   "hold_s": 2.0, "target_topic": "halloween/eyes/target",
                   "command_topic": "halloween/eyes/command", "host": "192.168.1.10",
                   "port": "1883", "url": "ws://homeassistant.local:8123/api/websocket"}

BOOLS = {"1", "yes", "true", "on", "0", "no", "false", "off"}

# ---- output -------------------------------------------------------------------
TTY = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if TTY else s
OK, WARN, ERR, INFO = _c("32", "  ok  "), _c("33", " warn "), _c("31", "ERROR "), _c("36", " info ")


class Report:
    def __init__(self, name):
        self.name, self.lines, self.errors, self.warnings = name, [], 0, 0

    def ok(self, msg):   self.lines.append((OK, msg))
    def info(self, msg): self.lines.append((INFO, msg))
    def warn(self, msg): self.lines.append((WARN, msg)); self.warnings += 1
    def err(self, msg):  self.lines.append((ERR, msg)); self.errors += 1

    def show(self):
        print(f"\n== {self.name}")
        for tag, msg in self.lines:
            print(f"  [{tag}] {msg}")


# ---- gathering from a Pi -------------------------------------------------------
GATHER = r"""
cd ~/%(dir)s 2>/dev/null || { echo "===NODIR==="; exit 0; }
echo; echo "===CONFIG==="; cat config.ini 2>/dev/null || echo "__MISSING__"
echo; echo "===SERVICE==="; systemctl cat demoneye 2>/dev/null
echo; echo "===ACTIVE==="; systemctl is-active demoneye 2>/dev/null
echo; echo "===DRM==="
for f in /sys/class/drm/card*-*/status; do [ -e "$f" ] && echo "$(basename "$(dirname "$f")") $(cat "$f")"; done
echo; echo "===IRCTL==="; command -v ir-ctl || true
echo; echo "===LIRC==="; ls /dev/lirc* 2>/dev/null || true
echo; echo "===CMDLINE_BOOTED==="; cat /proc/cmdline 2>/dev/null
echo; echo "===CMDLINE_FILE==="; cat /boot/firmware/cmdline.txt 2>/dev/null || cat /boot/cmdline.txt 2>/dev/null
echo; echo "===BRIDGE_INI==="; cat bridge.ini 2>/dev/null || echo "__MISSING__"
echo; echo "===BRIDGE_SERVICE==="; systemctl cat ha-bridge 2>/dev/null
echo; echo "===BRIDGE_ENABLED==="; systemctl is-enabled ha-bridge 2>/dev/null
echo; echo "===BRIDGE_ACTIVE==="; systemctl is-active ha-bridge 2>/dev/null
echo; echo "===BRIDGE_ENV==="; stat -c %%s /etc/ha-bridge.env 2>/dev/null || echo "__MISSING__"
echo; echo "===BRIDGE_DEPS==="
if [ -x venv/bin/python ]; then o=$(venv/bin/python -c "import websockets, paho.mqtt" 2>&1) && echo "__OK__" || echo "$o" | tail -1; else echo "no venv"; fi
echo; echo "===BRIDGE_LOG==="; journalctl -u ha-bridge -n 3 --no-pager -o cat 2>/dev/null
echo; echo "===END==="
"""


def ssh(host, script, timeout=15):
    dest = f"{USER_NAME}@{host}"
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", dest, "bash -s"],
                           input=script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "ssh timed out"
    if r.returncode == 255:
        return None, (r.stderr.strip().splitlines() or ["ssh failed"])[-1]
    return r.stdout, None


def sections(text):
    """Split the gathered output on ===NAME=== markers. A marker can end up
    glued to the end of the previous file's last line when that file has no
    trailing newline (cmdline.txt usually doesn't), so match it anywhere."""
    parts = re.split(r"===([A-Z_]+)===\n?", text)
    return {parts[i]: parts[i + 1].strip("\n") for i in range(1, len(parts) - 1, 2)}


def remote_checks(host, cp, ha_url=None):
    """Second round-trip: checks that need values from the config."""
    keymap = cp.get("projector", "keymap", fallback="")
    atlas = cp.get("stage", "figure_atlas", fallback="")
    mhost = cp.get("mqtt", "host", fallback="")
    mport = cp.get("mqtt", "port", fallback="1883")
    q = shlex.quote
    script = f"""
cd ~/{REMOTE_DIR} 2>/dev/null
echo "KEYMAP $( [ -n {q(keymap)} ] && [ -f {q(keymap)} ] && echo yes || echo no )"
echo "ATLAS $( [ -n {q(atlas)} ] && [ -f {q(atlas)} ] && echo yes || echo no )"
echo "BROKER $(timeout 3 bash -c 'exec 3<>/dev/tcp/'{q(mhost)}'/'{q(mport)} 2>/dev/null && echo yes || echo no)"
"""
    if ha_url:
        u = urlparse(ha_url)
        hh, hp = u.hostname or "", u.port or (443 if u.scheme in ("wss", "https") else 80)
        script += f"""echo "HA $(timeout 3 bash -c 'exec 3<>/dev/tcp/'{q(hh)}'/'{q(str(hp))} 2>/dev/null && echo yes || echo no)"
"""
    out, _ = ssh(host, script)
    res = {}
    for line in (out or "").splitlines():
        k, _, v = line.partition(" ")
        res[k] = v.strip()
    return res


# ---- per-file checks -----------------------------------------------------------
def parse_and_check(text, rep, schema=SCHEMA, fname="config.ini", optional=()):
    """Parse exactly as the app does (plain ConfigParser), then validate."""
    SCHEMA = schema
    cp = configparser.ConfigParser()
    try:
        cp.read_string(text)
    except configparser.Error as e:
        rep.err(f"{fname} doesn't parse: {e}".splitlines()[0])
        return None

    problems_before = rep.errors + rep.warnings
    for sec in cp.sections():
        if sec not in SCHEMA:
            near = difflib.get_close_matches(sec, SCHEMA, n=1)
            rep.warn(f"{fname}: [{sec}] is not a section it reads" + (f" (did you mean [{near[0]}]?)" if near else ""))
            continue
        for key, raw in cp.items(sec, raw=True):
            if key in cp.defaults():
                continue
            if key not in SCHEMA[sec]:
                near = difflib.get_close_matches(key, SCHEMA[sec], n=1)
                rep.err(f"{fname} [{sec}] {key}: unknown key — it will be ignored"
                        + (f" (did you mean {near[0]}?)" if near else ""))
                continue
            kind, rule = SCHEMA[sec][key]
            val = raw.strip()
            if re.search(r"\s[;#]", raw) or (kind != S and (";" in val or "#" in val)):
                rep.err(f"{fname} [{sec}] {key} = {raw!r}: inline comment — it's kept as part of "
                        f"the value. Put comments on their own line.")
                continue
            if kind == B and val.lower() not in BOOLS:
                rep.err(f"{fname} [{sec}] {key} = {val!r}: must be true/false")
            elif kind in (F, I):
                try:
                    num = float(val) if kind == F else int(val)
                except ValueError:
                    rep.err(f"{fname} [{sec}] {key} = {val!r}: must be {'a number' if kind == F else 'a whole number'}")
                    continue
                if isinstance(rule, tuple) and not (rule[0] <= num <= rule[1]):
                    rep.warn(f"{fname} [{sec}] {key} = {val}: outside the usual range {rule[0]}–{rule[1]}")
            elif kind == S and isinstance(rule, set) and val.lower() not in rule:
                rep.err(f"{fname} [{sec}] {key} = {val!r}: must be one of {sorted(rule)}")
    for sec in SCHEMA:
        if not cp.has_section(sec) and sec not in optional:
            rep.info(f"{fname}: no [{sec}] section — built-in defaults apply")
    if rep.errors + rep.warnings == problems_before:
        rep.ok(f"{fname}: every key is known, typed correctly and in range")
    return cp


def g(cp, sec, key, kind=S, default=None):
    try:
        if kind == B: return cp.getboolean(sec, key)
        if kind == F: return cp.getfloat(sec, key)
        if kind == I: return cp.getint(sec, key)
        return cp.get(sec, key).strip()
    except (configparser.Error, ValueError):
        return default


def parse_service(text):
    """Role / mode / eye and the KMSDRM index from the installed unit."""
    exec_line, idx = "", None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("ExecStart=") and s != "ExecStart=":
            exec_line = s[len("ExecStart="):]
        m = re.match(r"Environment=\"?SDL_KMSDRM_DEVICE_INDEX=(\d+)", s)
        if m:
            idx = int(m.group(1))
    args = shlex.split(exec_line) if exec_line else []
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default
    return {"role": opt("--role"), "mode": opt("--mode", "eyes"), "eye": opt("--eye", "demon"),
            "start": opt("--start"), "kms_index": idx, "found": bool(exec_line)}


def check_forced_hdmi(rep, sec, conns, cp):
    """The HDMI hot-plug state tells us whether the projector is really on, and
    ir_control uses it to verify every power command (POWER is a toggle). A
    forced video mode (video=HDMI-A-n:...D) makes the port always read
    "connected", which blinds that — so with IR verification on, it should go.
    Without IR it's harmless (and lets the app start before the projector)."""
    booted = sec.get("CMDLINE_BOOTED", "")
    filetxt = sec.get("CMDLINE_FILE", "")
    if not booted and not filetxt:
        return                                          # not a Pi / couldn't read
    lines = [l for l in filetxt.splitlines() if l.strip()]
    if len(lines) > 1:
        rep.err("cmdline.txt has more than one line — the kernel only reads the FIRST; "
                "anything on later lines is ignored. Join it onto the end of line 1")
    forced = lambda text: re.findall(r"video=(HDMI-A-\d+):\S*?D\b", text)
    now, nxt = forced(booted), forced(lines[0] if lines else "")
    ir_on = bool(cp) and g(cp, "projector", "ir_enabled", B, False)
    verify = bool(cp) and g(cp, "projector", "verify", B, True)
    if ir_on and verify:
        if now or nxt:
            where = "is active" if now else "is set in cmdline.txt (active after reboot)"
            rep.warn(f"forced HDMI mode {where} ({', '.join(now or nxt)}): the port always reads "
                     "'connected', so IR power commands can't be verified and fall back to blind "
                     "toggles. Remove video=...D from /boot/firmware/cmdline.txt and reboot")
        else:
            rep.ok("HDMI state is live — IR power commands are verified against it")
    elif now:
        rep.info(f"HDMI forced on ({', '.join(now)}) — fine without IR verification")
    elif nxt:
        rep.info(f"cmdline.txt forces {', '.join(nxt)} on, active after a reboot")


def check_bridge(rep, sec, eye_cp):
    """The tracking bridge on this Pi. Returns {"runs": bool, "state": str, "cp": ...}."""
    ini = sec.get("BRIDGE_INI", "__MISSING__")
    has_ini = ini.strip() != "__MISSING__"
    unit = sec.get("BRIDGE_SERVICE", "").strip()
    enabled = sec.get("BRIDGE_ENABLED", "").strip()
    active = sec.get("BRIDGE_ACTIVE", "").strip()
    runs = enabled == "enabled" or active in ("active", "activating")
    info = {"runs": runs, "state": f"{enabled or 'not installed'}/{active or '?'}", "cp": None}

    if not unit:
        if has_ini:
            rep.info("bridge: bridge.ini is here but ha-bridge.service isn't installed "
                     "(fine if the bridge runs on the other Pi)")
        else:
            rep.info("bridge: not installed on this Pi")
        return info

    # service state
    if enabled == "enabled" and active == "active":
        rep.ok("bridge: ha-bridge.service installed, enabled and running")
    elif active == "active":
        rep.warn("bridge: running but not enabled — it won't come back after a reboot "
                 "(sudo systemctl enable ha-bridge)")
    elif enabled == "enabled":
        log = sec.get("BRIDGE_LOG", "").strip().splitlines()
        rep.err(f"bridge: enabled but {active or 'not running'}"
                + (f" — last log: {log[-1][:160]}" if log else "")
                + "  (journalctl -u ha-bridge -n 30)")
    else:
        rep.info(f"bridge: ha-bridge.service installed but {enabled or 'disabled'} / {active or 'stopped'}")
    if not runs:
        return info

    m = re.search(r"^ExecStart=(.+)$", unit, re.M)
    if m and "ha_bridge.py" not in m.group(1):
        rep.err(f"bridge: ExecStart doesn't run ha_bridge.py: {m.group(1)}")
    elif m and f"/{REMOTE_DIR}/" not in m.group(1):
        rep.warn(f"bridge: ExecStart isn't under ~/{REMOTE_DIR} — deploy.sh won't update it: {m.group(1)}")
    u = re.search(r"^User=(\S+)", unit, re.M)
    if u and u.group(1) != USER_NAME:
        rep.warn(f"bridge: service runs as {u.group(1)}, not {USER_NAME}")

    deps = sec.get("BRIDGE_DEPS", "").strip()
    if deps == "__OK__":
        rep.ok("bridge: Python deps (websockets, paho-mqtt) installed in the venv")
    else:
        rep.err(f"bridge: Python deps missing ({deps or 'unknown'}) — "
                "./venv/bin/pip install -r requirements-bridge.txt")

    # bridge.ini
    if not has_ini:
        rep.warn("bridge: no bridge.ini — running on built-in defaults (copy yours over: "
                 "deploy.sh never copies it)")
        bcp = configparser.ConfigParser()
    else:
        bcp = parse_and_check(ini, rep, BRIDGE_SCHEMA, "bridge.ini")
        if bcp is None:
            return info
    info["cp"] = bcp
    bridge_values(rep, bcp, eye_cp, sec)
    return info


def bg(bcp, s, k, kind=S):
    """bridge.ini value with ha_bridge.py's own defaults."""
    return g(bcp, s, k, kind, BRIDGE_DEFAULTS.get(k)) if bcp is not None else BRIDGE_DEFAULTS.get(k)


def bridge_values(rep, bcp, eye_cp, sec=None):
    """Value checks shared by the Pi check and --file mode."""
    token = (g(bcp, "home_assistant", "token") or "") if bcp.has_section("home_assistant") else ""
    if sec is not None:
        env = sec.get("BRIDGE_ENV", "__MISSING__").strip()
        env_ok = env.isdigit() and int(env) > 0
        if token:
            rep.warn("bridge: HA token is in bridge.ini — better in /etc/ha-bridge.env "
                     "(root-only) and blank in bridge.ini")
        elif env_ok:
            rep.ok("bridge: HA token comes from /etc/ha-bridge.env")
        else:
            rep.err("bridge: no HA token — bridge.ini token is blank and /etc/ha-bridge.env is "
                    + ("empty" if env.isdigit() else "missing")
                    + ". sudo install -m 600 /dev/null /etc/ha-bridge.env; "
                      "echo 'HA_TOKEN=...' | sudo tee -a /etc/ha-bridge.env")
    elif token:
        rep.warn("HA token is in this file — keep it in /etc/ha-bridge.env instead")

    for k in ("x_pattern", "y_pattern"):
        v = g(bcp, "entities", k)
        if v and "{n}" not in v:
            rep.err(f"bridge.ini [entities] {k} = {v!r}: needs {{n}} where the target number goes")

    if not bg(bcp, "mqtt", "follow_command", B):
        rep.warn("bridge: follow_command = false — it streams targets all day, even with the "
                 "show off. Set true so the on/off command controls it")
    else:
        rep.ok("bridge: follows the on/off command — silent (and disconnected from HA) while off")
        if bg(bcp, "mqtt", "start_enabled", B):
            rep.info("bridge: start_enabled = true — it tracks from startup until the first 'off'")

    if eye_cp is not None:
        pairs = [("host", "host"), ("port", "port"), ("target_topic", "target_topic")]
        if bg(bcp, "mqtt", "follow_command", B):
            pairs.append(("command_topic", "command_topic"))
        for bk, ek in pairs:
            bv, ev = str(bg(bcp, "mqtt", bk)), g(eye_cp, "mqtt", ek)
            if ev is not None and bv != ev:
                rep.err(f"bridge: [mqtt] {bk} = {bv} but the eyes' config.ini has {ev} — "
                        "the eyes won't hear it")
        hold = g(eye_cp, "control", "target_hold", F)
        if hold is not None:
            hb, bh = bg(bcp, "calibration", "heartbeat_s", F), bg(bcp, "calibration", "hold_s", F)
            if hb is not None and hb >= hold:
                rep.warn(f"bridge: heartbeat_s ({hb}) should be below the eyes' target_hold ({hold}) "
                         "or someone standing still gets dropped")
            if bh is not None and abs(bh - hold) > 1e-6:
                rep.info(f"bridge: hold_s ({bh}) differs from the eyes' target_hold ({hold})")


def check_pi(host):
    rep = Report(host)
    out, err = ssh(host, GATHER % {"dir": REMOTE_DIR})
    if out is None:
        rep.err(f"can't reach over ssh without a password: {err}")
        return rep, None, None
    sec = sections(out)
    if "NODIR" in sec:
        rep.err(f"~/{REMOTE_DIR} doesn't exist on this Pi — run ./deploy.sh first")
        return rep, None, None
    if sec.get("CONFIG", "").strip() == "__MISSING__":
        rep.err(f"no ~/{REMOTE_DIR}/config.ini — copy one over and edit it")
        return rep, None, None

    cp = parse_and_check(sec.get("CONFIG", ""), rep)
    svc = parse_service(sec.get("SERVICE", ""))

    # service
    if not svc["found"]:
        rep.warn("demoneye.service isn't installed (or has no ExecStart) — it won't start on boot")
    else:
        if svc["role"] not in ("brain", "follower"):
            rep.err(f"service has no valid --role (got {svc['role']!r})")
        else:
            rep.ok(f"service: --role {svc['role']} --mode {svc['mode']} --eye {svc['eye']}")
        active = sec.get("ACTIVE", "").strip()
        (rep.ok if active == "active" else rep.warn)(f"service is {active or 'unknown'}")

    # display card vs KMSDRM index
    conns = [l.split() for l in sec.get("DRM", "").splitlines() if l.strip()]
    hdmi = [(c, st) for c, st in conns if "-HDMI-A-" in c]
    display_cards = sorted({int(re.match(r"card(\d+)", c).group(1)) for c, _ in hdmi})
    names = [c for c, st in hdmi if st == "connected"]
    if not conns:
        rep.info("couldn't read /sys/class/drm (not a Pi?)")
    else:
        if names:
            rep.ok(f"projector is ON — connected on {', '.join(names)}")
        else:
            rep.info("no projector on HDMI right now — normal if it's switched off; "
                     "if it's on, check the cable")
        if svc["found"] and svc["kms_index"] is not None and display_cards \
                and svc["kms_index"] not in display_cards:
            rep.err(f"service sets SDL_KMSDRM_DEVICE_INDEX={svc['kms_index']} but the HDMI ports are on "
                    f"card{display_cards[0]} — set it to {display_cards[0]}")

    check_forced_hdmi(rep, sec, conns, cp)
    svc["bridge"] = check_bridge(rep, sec, cp)

    if cp is None:
        return rep, None, svc

    # start mode
    start = svc.get("start") or g(cp, "control", "start", S, "playing")
    if start == "playing":
        rep.info("start = playing: renders at boot. With Node-RED in charge, start = idle "
                 "keeps the house dark until the 'on' command")

    # values that need the Pi itself
    bcp = svc["bridge"]["cp"] if svc["bridge"]["runs"] else None
    rc = remote_checks(host, cp, bg(bcp, "home_assistant", "url") if svc["bridge"]["runs"] else None)
    mh = g(cp, "mqtt", "host", S, "?")
    if rc.get("BROKER") == "yes":
        rep.ok(f"broker {mh}:{g(cp, 'mqtt', 'port', S, '1883')} reachable from this Pi")
    else:
        rep.err(f"broker {mh}:{g(cp, 'mqtt', 'port', S, '1883')} NOT reachable from this Pi")

    if svc["bridge"]["runs"]:
        url = bg(bcp, "home_assistant", "url")
        if rc.get("HA") == "yes":
            rep.ok(f"bridge: Home Assistant reachable ({urlparse(url).netloc})")
        else:
            rep.err(f"bridge: Home Assistant NOT reachable from this Pi ({url})")

    if g(cp, "projector", "ir_enabled", B, False):
        if not sec.get("IRCTL", "").strip():
            rep.err("ir_enabled = true but ir-ctl isn't installed (sudo apt install v4l-utils)")
        if not sec.get("LIRC", "").strip():
            rep.err("ir_enabled = true but there's no /dev/lirc* (dtoverlay=gpio-ir-tx in config.txt?)")
        if rc.get("KEYMAP") != "yes":
            rep.err(f"ir keymap not found: {g(cp, 'projector', 'keymap')}")
        if sec.get("IRCTL", "").strip() and sec.get("LIRC", "").strip() and rc.get("KEYMAP") == "yes":
            rep.ok("IR: ir-ctl, transmitter device and keymap all present")
        if g(cp, "projector", "off_confirm", I, 0) < 1:
            rep.info("off_confirm = 0 — your projector needs POWER pressed twice to turn off")
    else:
        rep.info("IR projector control is off on this Pi")

    if svc.get("mode") == "stage" and g(cp, "stage", "figure_atlas") and rc.get("ATLAS") != "yes" \
            and svc.get("eye") in (None, "sprite", "demon"):
        rep.err(f"figure_atlas not found on this Pi: {g(cp, 'stage', 'figure_atlas')}")
    return rep, cp, svc


# ---- pair checks ---------------------------------------------------------------
def check_bridge_pair(rep, pis):
    seen = [(h, svc["bridge"]) for h, _, svc in pis if svc and "bridge" in svc]
    if not seen:
        return
    runners = [h for h, b in seen if b["runs"]]
    if len(runners) == 1:
        rep.ok(f"tracking bridge runs on exactly one Pi ({runners[0]})")
    elif len(runners) > 1:
        rep.err(f"tracking bridge runs on {', '.join(runners)} — two bridges publish competing "
                "targets. Keep one: sudo systemctl disable --now ha-bridge on the other")
    elif len(seen) == len(PIS):
        rep.warn("tracking bridge isn't running on either Pi — the eyes won't follow anyone "
                 "(fine if you run it on another machine)")


def check_pair(pis):
    rep = Report("the pair")
    check_bridge_pair(rep, pis)
    live = [(h, cp, svc) for h, cp, svc in pis if cp is not None]
    if len(live) < 2:
        rep.info("need two readable configs to compare the pair")
        return rep

    roles = [(h, (svc or {}).get("role")) for h, _, svc in live]
    brains = [h for h, r in roles if r == "brain"]
    if any(r for _, r in roles):
        if len(brains) == 1:
            rep.ok(f"exactly one brain ({brains[0]})")
        elif not brains:
            rep.err("no Pi is the brain — nothing will move. Set --role brain on one")
        else:
            rep.err(f"more than one brain ({', '.join(brains)}) — they'll fight. Make one a follower")

    shows = {h: ((svc or {}).get("mode"), (svc or {}).get("eye")) for h, _, svc in live if svc and svc.get("found")}
    if len(set(shows.values())) > 1:
        rep.err("different shows: " + ", ".join(f"{h} {m}/{e}" for h, (m, e) in shows.items())
                + " — use the same --mode and --eye on both")
    elif shows:
        rep.ok(f"same show on both ({next(iter(shows.values()))[0]}/{next(iter(shows.values()))[1]})")

    for key in ("host", "port", "topic", "command_topic", "target_topic"):
        vals = {h: g(cp, "mqtt", key) for h, cp, _ in live}
        if len(set(vals.values())) > 1:
            rep.err(f"[mqtt] {key} differs: " + ", ".join(f"{h}={v}" for h, v in vals.items())
                    + " — they won't hear each other")
    if all(len({g(cp, 'mqtt', k) for _, cp, _ in live}) == 1 for k in ("host", "port", "topic")):
        rep.ok("same broker and topics")

    mirrors = [h for h, cp, _ in live if g(cp, "display", "mirror", B, False)]
    if len(mirrors) > 1:
        rep.warn("mirror = true on both Pis — the pair won't be symmetric. Set it on one only")
    elif not mirrors:
        rep.info("mirror is off on both — fine, but set it on ONE Pi for a symmetric pair of eyes")
    else:
        rep.ok(f"mirror on one Pi only ({mirrors[0]})")
    for h, cp, _ in live:
        if g(cp, "display", "mirror_gaze", B, False):
            rep.warn(f"{h}: mirror_gaze = true — the eyes will look in opposite directions")

    for key in ("gaze_scale", "gaze_scale_y"):
        vals = {h: g(cp, "display", key, F) for h, cp, _ in live}
        if len(set(vals.values())) > 1:
            rep.info(f"[display] {key} differs ({', '.join(f'{h}={v}' for h, v in vals.items())}) — "
                     "fine only if the projectors are at different distances")
    vals = {h: g(cp, "control", "track_look_down", F) for h, cp, _ in live}
    if len(set(vals.values())) > 1:
        rep.info(f"track_look_down differs ({', '.join(f'{h}={v}' for h, v in vals.items())}) — "
                 "fine only if the eyes are at different heights")
    starts = {h: g(cp, "control", "start") for h, cp, _ in live}
    if len(set(starts.values())) > 1:
        rep.warn("start differs: " + ", ".join(f"{h}={v}" for h, v in starts.items()))

    file_mode = all(svc is None for _, _, svc in live)
    if file_mode or any((svc or {}).get("mode") == "stage" for _, _, svc in live):
        spans = sorted(((g(cp, "stage", "stage_from", F, 0.0), g(cp, "stage", "stage_to", F, 1.0), h)
                        for h, cp, _ in live))
        for a, b, h in spans:
            if not a < b:
                rep.err(f"{h}: stage_from ({a}) must be less than stage_to ({b})")
        for (a1, b1, h1), (a2, b2, h2) in zip(spans, spans[1:]):
            if a2 < b1:
                rep.err(f"stage spans overlap: {h1} {a1}–{b1} and {h2} {a2}–{b2} — "
                        "a figure would appear in both windows at once")
            elif a2 == b1:
                rep.warn(f"no gap between {h1} and {h2} — the wall between the windows is the effect")
            else:
                rep.ok(f"stage spans {h1} {a1}–{b1}, {h2} {a2}–{b2} (wall gap {a2 - b1:.2f})")
    return rep


# ---- main ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Verify config.ini on each Pi")
    ap.add_argument("hosts", nargs="*", help="Pis to check (default: both)")
    ap.add_argument("--file", nargs="+", metavar="INI", help="check local files instead of Pis")
    a = ap.parse_args()

    reports, pis = [], []
    if a.file:
        for path in a.file:
            rep = Report(path)
            try:
                text = open(path).read()
            except OSError as e:
                rep.err(str(e)); reports.append(rep); continue
            if re.search(r"^\[(home_assistant|calibration|entities)\]", text, re.M):
                bcp = parse_and_check(text, rep, BRIDGE_SCHEMA, "bridge.ini")
                if bcp is not None:
                    bridge_values(rep, bcp, None)
                reports.append(rep)
                continue
            cp = parse_and_check(text, rep)
            reports.append(rep); pis.append((path, cp, None))
    else:
        for host in (a.hosts or PIS):
            rep, cp, svc = check_pi(host)
            reports.append(rep); pis.append((host, cp, svc))

    reports.append(check_pair(pis))
    for r in reports:
        r.show()
    errors = sum(r.errors for r in reports)
    warnings = sum(r.warnings for r in reports)
    print()
    if errors:
        print(_c("31", f"{errors} error(s), {warnings} warning(s) — fix the errors before the show"))
        sys.exit(1)
    print(_c("32", f"no errors") + (f", {warnings} warning(s)" if warnings else " — good to go"))


if __name__ == "__main__":
    main()
