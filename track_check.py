#!/usr/bin/env python3
"""
Why aren't the eyes tracking? Read-only check of every link in the chain,
safe to run remotely and in daylight. It never turns the show on, never
publishes a target, and never moves the eyes.

Run it ON the Pi that runs the tracking bridge (pi-eye-right.home):

    cd ~/demoneye
    sudo ./venv/bin/python track_check.py                 # since yesterday 17:00
    sudo ./venv/bin/python track_check.py --since "2026-10-06 18:00"
    sudo ./venv/bin/python track_check.py --simulate      # fake a walk-by (see below)

sudo is only so it can read the HA token in /etc/ha-bridge.env and the
journal; nothing is changed.

The chain, in order:
  1. Node-RED / HA sends "on" on the command topic   -> bridge log "tracking enabled"
  2. bridge connects to Home Assistant               -> "[ha] authenticated"
  3. it finds the radar's target entities            -> "[ha] seeded N entities"
  4. the radar reports someone (HA history)          -> non-zero target X/Y
  5. bridge turns that into a target and publishes   -> "[tgt] acquired"
  6. the brain (left Pi) receives it and looks       -> brain log "[track] ..."
The first link that's missing is the problem.

--simulate fakes a person walking past the radar: it briefly overwrites the
first target's X/Y in Home Assistant (left -> right), then puts them back to 0.
With the show on, that should produce "[tgt] acquired" in the bridge log and
"[track] target received" in the brain's (left Pi) log, and the eyes look.
The radar overwrites the values on its next real update. Nothing else changes.
"""

import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TTY = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if TTY else s
OK, WARN, BAD, INFO = _c("32", "  ok  "), _c("33", " warn "), _c("31", " BAD  "), _c("36", " info ")
FINDINGS = []


def say(tag, msg):
    print(f"  [{tag}] {msg}")


def find(msg):
    FINDINGS.append(msg)


def load_token_env():
    """Pick up HA_TOKEN from the service's env file when run with sudo."""
    if os.environ.get("HA_TOKEN"):
        return
    try:
        for line in open("/etc/ha-bridge.env"):
            k, _, v = line.strip().partition("=")
            if k == "HA_TOKEN" and v:
                os.environ["HA_TOKEN"] = v.strip().strip('"').strip("'")
    except OSError:
        pass


def http_base(ws_url):
    u = urllib.parse.urlparse(ws_url)
    scheme = "https" if u.scheme == "wss" else "http"
    return f"{scheme}://{u.netloc}"


def ha_get(base, token, path, timeout=10):
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def journal(unit, since):
    try:
        r = subprocess.run(["journalctl", "-u", unit, "--since", since, "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    yday = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d 17:00")
    ap.add_argument("--since", default=yday, help=f'start of the window (default "{yday}")')
    ap.add_argument("--config", default=os.path.join(HERE, "bridge.ini"))
    ap.add_argument("--simulate", action="store_true",
                    help="fake a walk-by in Home Assistant and check the bridge reacts")
    a = ap.parse_args()

    load_token_env()
    import ha_bridge  # noqa: E402  (after HA_TOKEN is in the environment)
    cfg = ha_bridge.Config(a.config)
    since_dt = dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M")

    print(f"\nChecking tracking since {a.since} (local time) on {socket.gethostname()}")

    # ---- the bridge's own log ------------------------------------------------
    print("\n== 1-3, 5. the bridge's log (ha-bridge.service)")
    lines = journal("ha-bridge", a.since)
    has_log = any(re.search(r"\[(cfg|bridge|ha|tgt|status)\]", l) for l in lines)
    if not has_log:
        say(BAD, "no ha-bridge log in that window — was the service running? "
                 "(systemctl status ha-bridge) or run with sudo to read the journal")
        find("The bridge has no log for that period: not running, or not readable without sudo.")
    pat = {
        "enabled": r"tracking enabled", "disabled": r"tracking disabled",
        "auth": r"\[ha\] authenticated", "seeded": r"\[ha\] seeded (\d+)",
        "acquired": r"\[tgt\] acquired", "cleared": r"\[tgt\] cleared",
        "ha_err": r"\[ha\] disconnected: ", "show_off": r"show is off",
        "trace": r"Traceback|Error|error:",
        "following": r"\[bridge\] following",
    }
    hits = {k: [l for l in lines if re.search(p, l)] for k, p in pat.items()}
    seeded = [int(re.search(pat["seeded"], l).group(1)) for l in hits["seeded"]]
    seeing_since = None          # first time the bridge found ALL its entities
    for l in hits["seeded"]:
        if int(re.search(pat["seeded"], l).group(1)) >= len(cfg.entities):
            try:
                seeing_since = dt.datetime.fromisoformat(l.split()[0])
            except ValueError:
                pass
            break
    for k, label in (("following", "listening to the command topic"), ("enabled", "'on' received (tracking enabled)"),
                     ("disabled", "'off' received"), ("auth", "connected to Home Assistant"),
                     ("seeded", "sensor entities found"), ("acquired", "targets acquired"),
                     ("cleared", "targets released"), ("ha_err", "Home Assistant errors")):
        extra = f"  (last: {max(seeded) if seeded else 0} of {len(cfg.entities)} entities)" if k == "seeded" and seeded else ""
        print(f"     {label:36s} {len(hits[k]):5d}{extra}")
    if has_log and not hits["enabled"]:
        say(BAD, "the bridge never heard 'on' — it stays silent (and disconnected from HA) until it does")
        find("Link 1: the bridge never received 'on' on the command topic. Check Node-RED publishes to "
             f"'{cfg.command_topic}' on the same broker the bridge uses ({cfg.mqtt_host}).")
    elif hits["enabled"] and not hits["auth"]:
        say(BAD, "'on' arrived but it never connected to Home Assistant")
        for l in hits["ha_err"][-3:]:
            print("       " + l.split("]: ", 1)[-1][:150])
        find("Link 2: the bridge can't log in to Home Assistant (token, URL, or HA unreachable). See errors above.")
    elif hits["auth"] and (not seeded or max(seeded) < len(cfg.entities)):
        say(BAD, f"Home Assistant has only {max(seeded) if seeded else 0} of the {len(cfg.entities)} "
                 "target entities the bridge is looking for")
        find("Link 3: entity names in bridge.ini don't match Home Assistant (see section 4 for which).")
    elif hits["auth"]:
        say(OK, "heard 'on', logged in, found all entities")
    if hits["trace"]:
        say(WARN, f"{len(hits['trace'])} error line(s), last: {hits['trace'][-1][:150]}")

    # ---- Home Assistant: entities and last night's radar readings --------------
    print("\n== 4. the radar in Home Assistant")
    token = cfg.token
    base = http_base(cfg.url)
    if not token:
        say(BAD, "no HA token (run with sudo so /etc/ha-bridge.env can be read)")
        find("No HA token available to this check — rerun with sudo.")
    else:
        try:
            ha_get(base, token, "/api/")
            say(OK, f"Home Assistant API reachable at {base}")
        except urllib.error.HTTPError as e:
            say(BAD, f"Home Assistant rejected the token ({e.code}) — create a new long-lived token")
            find("The HA token is rejected (expired/revoked?). Make a new one; put it in /etc/ha-bridge.env.")
            token = None
        except Exception as e:
            say(BAD, f"can't reach Home Assistant at {base}: {e}")
            find(f"Home Assistant isn't reachable at {base} from this Pi.")
            token = None
    if token:
        missing = []
        for ent in sorted(cfg.entities):
            try:
                st = ha_get(base, token, f"/api/states/{ent}")
                print(f"     {ent:52s} now: {st.get('state')}")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    missing.append(ent)
                    print(f"     {ent:52s} " + _c("31", "NOT FOUND"))
        if missing:
            find("These entities don't exist in HA: " + ", ".join(missing) +
                 ". Find the real names in HA -> Developer tools -> States (search 'target').")
        xs = [t["x"] for t in cfg.targets]
        start_iso = since_dt.astimezone().isoformat()
        q = urllib.parse.urlencode({"filter_entity_id": ",".join(xs + [t["y"] for t in cfg.targets]),
                                    "minimal_response": "", "no_attributes": ""})
        try:
            hist = ha_get(base, token, f"/api/history/period/{urllib.parse.quote(start_iso)}?{q}", timeout=30)
        except Exception as e:
            hist = []
            say(WARN, f"couldn't read history: {e}")
        seen_any = False
        seen_after = 0
        for series in hist:
            if not series:
                continue
            ent = series[0].get("entity_id", "?")
            vals, after = [], 0
            for s in series:
                try:
                    v = float(s.get("state"))
                except (TypeError, ValueError):
                    continue
                if abs(v) >= 1.0:
                    vals.append((s.get("last_changed", ""), v))
                    try:
                        if seeing_since and dt.datetime.fromisoformat(s["last_changed"]) >= seeing_since:
                            after += 1
                    except (KeyError, ValueError):
                        pass
            if ent in xs:
                if vals:
                    seen_any = True
                    first, last = vals[0][0][11:16], vals[-1][0][11:16]
                    lo, hi = min(v for _, v in vals), max(v for _, v in vals)
                    print(f"     {ent:52s} {len(vals):5d} readings with someone in view "
                          f"(UTC {first}-{last}, X {lo:+.0f}..{hi:+.0f} mm)")
                    if seeing_since:
                        print(f"     {'':52s} {after:5d} of them after the bridge could see the sensor "
                              f"({seeing_since.strftime('%H:%M')} local)")
                        seen_after = seen_after + after
                else:
                    print(f"     {ent:52s}     0 readings with someone in view")
        if hist and not seen_any:
            say(WARN, "the radar reported nobody in that window")
            find("Link 4: the radar's target X/Y never left 0 in HA. Either nobody walked through its "
                 "field of view, or 'Targets Position' reporting is off in the device's settings, or it's "
                 "aimed away from the approach.")
        elif seen_any:
            say(OK, "the radar did see people")
            if hits["auth"] and not hits["acquired"]:
                if seeing_since and not seen_after:
                    say(INFO, "nobody has been in view since the bridge could see the sensor — "
                              "wait for a passer-by, or run with --simulate")
                elif seen_after:
                    find("Link 5: the radar saw people while the bridge could see the sensor, but it "
                         "never acquired a target. If the show was on at those times, the bridge's target "
                         "logic is the problem — run with --simulate.")

    # ---- optional: fake a walk-by ---------------------------------------------
    if a.simulate and token:
        print("\n== simulated walk-by (Home Assistant -> bridge -> brain)")
        xe, ye = cfg.targets[0]["x"], cfg.targets[0]["y"]

        def post(ent, val):
            body = json.dumps({"state": str(val)}).encode()
            req = urllib.request.Request(base + f"/api/states/{ent}", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()

        t0 = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            post(ye, 1500)
            for x in (-200, -120, -40, 40, 120, 200):
                post(xe, x)
                time.sleep(0.7)
        finally:
            post(xe, 0)
            post(ye, 0)
        time.sleep(3)                     # let the bridge hold/clear and log
        got = [l for l in journal("ha-bridge", t0) if re.search(r"\[tgt\]", l)]
        if any("acquired" in l for l in got):
            say(OK, "the bridge acquired the fake person and published targets")
            for l in got[-3:]:
                print("       " + re.sub(r"^\S+\s+\S+\s+", "", l)[:120])
            print(f"     now on the LEFT Pi:  journalctl -t demoneye --since '{t0}' | grep track")
            print("     -> '[track] target received' there means the whole chain works.")
        else:
            say(BAD, "the bridge didn't react to the fake person")
            find("Simulated walk-by: no '[tgt] acquired'. Is the show on (bridge enabled)? "
                 "Check 'last bridge events' below.")

    # ---- MQTT: the command topic right now --------------------------------------
    print("\n== MQTT (right now)")
    try:
        import paho.mqtt.client as mqtt
        got = {}
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"track-check-{socket.gethostname()}")
        if cfg.mqtt_user:
            c.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass)
        c.on_connect = lambda cl, u, f, rc, p: (cl.subscribe(cfg.command_topic), cl.subscribe(cfg.topic))
        c.on_message = lambda cl, u, m: got.setdefault(m.topic, []).append((m.retain, m.payload[:80]))
        c.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=15)
        c.loop_start(); time.sleep(3); c.loop_stop(); c.disconnect()
        cmd = got.get(cfg.command_topic)
        if cmd:
            say(INFO, f"command topic '{cfg.command_topic}' holds {cmd[-1][1]!r}"
                      f"{' (retained)' if cmd[-1][0] else ''}")
        else:
            say(WARN, f"nothing retained on '{cfg.command_topic}' at {cfg.mqtt_host} — "
                      "Node-RED's 'on'/'off' should be published retained to THIS broker")
            find(f"No retained command on {cfg.mqtt_host}. If Node-RED still points at the old broker "
                 "(192.168.1.10), the bridge and eyes never hear on/off.")
        say(INFO, f"targets on '{cfg.topic}' in 3 s: {len(got.get(cfg.topic, []))} (0 is expected while the show is off)")
    except Exception as e:
        say(BAD, f"can't reach the MQTT broker {cfg.mqtt_host}:{cfg.mqtt_port}: {e}")
        find(f"The bridge's broker {cfg.mqtt_host} isn't reachable.")

    # ---- the most recent relevant bridge lines --------------------------------
    rel = [l for l in lines if re.search(r"\[(bridge|ha|tgt)\]|Traceback|rror", l)]
    if rel:
        print("\n== last bridge events")
        for l in rel[-12:]:
            print("     " + re.sub(r"^\S+\s+\S+\s+", "", l)[:160])

    print("\n== verdict")
    if FINDINGS:
        for f in FINDINGS:
            print("  * " + f)
    elif hits.get("acquired"):
        print("  * The bridge acquired and published targets. The break is after it: on the brain (left Pi).")
        print("    Check:  journalctl -t demoneye --since '%s' | grep -E 'track|target'" % a.since)
        print("    and that the brain's config.ini [mqtt] host / target_topic match bridge.ini.")
    else:
        print("  * No break found in the window. Run again after tonight's show with --since.")


if __name__ == "__main__":
    main()
