#!/usr/bin/env python3
"""
Find out why the two eyes aren't moving together. Run it ON a Pi while the
show is playing (it only listens, so the show keeps running):

    ./venv/bin/python sync_probe.py          # 15 s
    ./venv/bin/python sync_probe.py 30       # longer

It measures, from this Pi:
  1. the brain's state stream as it arrives here: rate, gaps and bunching
     (steady ~20 ms spacing is good; clumps of 0 ms then 100 ms are bad)
  2. round trip to the MQTT broker and back (MQTT RTT)
  3. plain network ping to the broker
  4. Wi-Fi: which interface, power saving, signal

…and says which of those is the likely cause. Run it on the follower first
(pi-eye-right.home); that's the one that lags. Then on the brain to compare.
"""

import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time

import paho.mqtt.client as mqtt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import settings as S  # noqa: E402

TTY = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if TTY else s
OK, WARN, BAD, INFO = _c("32", "  ok  "), _c("33", " warn "), _c("31", " BAD  "), _c("36", " info ")


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    S.load_config(os.path.join(HERE, "config.ini"))
    host = socket.gethostname()
    probe_topic = f"halloween/eyes/probe/{host}"

    arrivals, rtts = [], []
    lock = threading.Lock()

    def nodelay(c):
        try:
            c.socket().setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

    def make(name, topic, cb):
        """Separate connections: the state listener only receives (like the
        follower does), so the RTT probes can't distort what it measures."""
        cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sync-probe-{name}-{host}")
        if S.MQTT_USER:
            cl.username_pw_set(S.MQTT_USER, S.MQTT_PASS)
        cl.on_connect = lambda c, u, f, rc, p: (nodelay(c), c.subscribe(topic, qos=0))
        cl.on_message = cb
        cl.connect(S.MQTT_HOST, S.MQTT_PORT, keepalive=30)
        cl.loop_start()
        return cl

    def on_state(c, u, msg):
        with lock:
            arrivals.append(time.monotonic())

    def on_probe(c, u, msg):
        try:
            rtts.append((time.monotonic() - float(msg.payload)) * 1000)
        except ValueError:
            pass

    print(f"probing from {host} → broker {S.MQTT_HOST}:{S.MQTT_PORT} for {secs:.0f}s …", flush=True)
    listener = make("state", S.TOPIC, on_state)
    c = make("rtt", probe_topic, on_probe)

    # ping runs alongside the MQTT measurements
    ping_out = {}
    t = threading.Thread(target=lambda: ping_out.setdefault(
        "o", sh(["ping", "-c", str(int(secs * 4)), "-i", "0.25", "-q", S.MQTT_HOST])))
    t.start()

    time.sleep(1.0)                                  # let subscriptions settle
    with lock:
        arrivals.clear()
    start = time.monotonic()
    end = start + secs
    while time.monotonic() < end:
        c.publish(probe_topic, repr(time.monotonic()), qos=0)
        time.sleep(0.1)
    time.sleep(0.5)
    for cl in (c, listener):
        cl.loop_stop()
        cl.disconnect()
    t.join()

    verdicts = []
    print()

    # 1. state stream
    print("== state stream from the brain (as received here)")
    # only the measurement window: the listener keeps receiving while the ping
    # test finishes, and counting those inflated the rate (63.7/s for a 60/s brain)
    with lock:
        arrivals[:] = [t for t in arrivals if start <= t <= end]
    gaps = [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]
    rate = len(arrivals) / secs
    if len(arrivals) < 5:
        print(f"  [{INFO}] only {len(arrivals)} state messages — is the show playing? (this Pi may be "
              "the brain, which doesn't hear its own stream through the broker — run it on the follower)")
    else:
        sent = S.FPS / max(1, round(S.FPS / max(1.0, S.PUBLISH_HZ)))   # brain publishes per frame
        expect = 1000 / sent
        clumped = sum(1 for g in gaps if g < 3) / len(gaps)
        big = sum(1 for g in gaps if g > 100)
        print(f"  rate {rate:.1f}/s (brain sends {sent:.0f}/s, spacing {expect:.0f} ms)")
        print(f"  spacing ms: median {pct(gaps, 50):.0f}  p95 {pct(gaps, 95):.0f}  "
              f"p99 {pct(gaps, 99):.0f}  max {max(gaps):.0f}")
        print(f"  arrived bunched (<3 ms apart): {clumped:.0%}   gaps >100 ms: {big}")
        if rate < sent * 0.8:
            print(f"  [{WARN}] the brain sends one message per frame, so it's rendering only "
                  f"~{rate:.0f} fps (should be ~{S.FPS}) — or messages are being lost")
            verdicts.append("brain-rate")
        if clumped > 0.15 or pct(gaps, 95) > 2.5 * expect:
            print(f"  [{BAD}] messages arrive in clumps — they're being held back and released together")
            verdicts.append("bunching")
        elif big:
            print(f"  [{WARN}] {big} long gap(s) — occasional stalls")
            verdicts.append("stalls")
        else:
            print(f"  [{OK}] steady stream")

    # 2. MQTT RTT
    print("\n== round trip through the broker (this Pi → broker → this Pi)")
    if rtts:
        print(f"  ms: median {pct(rtts, 50):.1f}  p95 {pct(rtts, 95):.1f}  max {max(rtts):.1f}  "
              f"({len(rtts)} samples)")
        if pct(rtts, 95) > 40:
            print(f"  [{BAD}] slow")
            verdicts.append("mqtt-slow")
        elif pct(rtts, 95) > 15:
            print(f"  [{WARN}] a bit slow for a LAN")
            verdicts.append("mqtt-meh")
        else:
            print(f"  [{OK}] fast")
    else:
        print(f"  [{BAD}] no probe came back — check the broker connection")
        verdicts.append("mqtt-dead")

    # 3. ping
    print("\n== network ping to the broker")
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", ping_out.get("o", ""))
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", ping_out.get("o", ""))
    ping_p = None
    if m:
        mn, avg, mx, mdev = map(float, m.groups())
        ping_p = avg
        print(f"  ms: min {mn:.1f}  avg {avg:.1f}  max {mx:.1f}  jitter {mdev:.1f}  "
              f"loss {loss.group(1) if loss else '?'}%")
        if mx > 50 or mdev > 10 or (loss and float(loss.group(1)) > 0):
            print(f"  [{BAD}] slow or jittery network")
            verdicts.append("net")
        else:
            print(f"  [{OK}] network is fine")
    else:
        print(f"  [{INFO}] ping didn't run")

    # 4. Wi-Fi
    print("\n== network interface")
    route = sh(["ip", "route", "get", S.MQTT_HOST])
    dev = (re.search(r"\bdev (\S+)", route) or [None, None])[1]
    print(f"  traffic to the broker goes over: {dev or '?'}")
    if dev and dev.startswith("wl"):
        ps = sh(["iw", "dev", dev, "get", "power_save"]).strip()
        link = sh(["iw", "dev", dev, "link"])
        sig = (re.search(r"signal: (-?\d+) dBm", link) or [None, None])[1]
        rate_tx = (re.search(r"tx bitrate: ([\d.]+ \S+)", link) or [None, None])[1]
        print(f"  {ps or 'power save: ?'}   signal {sig or '?'} dBm   tx {rate_tx or '?'}")
        if "on" in ps.lower():
            print(f"  [{BAD}] Wi-Fi power saving is ON — the radio naps between packets, which adds "
                  "tens to hundreds of ms of delay")
            verdicts.append("powersave")
        if sig and int(sig) < -70:
            print(f"  [{WARN}] weak signal ({sig} dBm) — retries add delay")
            verdicts.append("weak")
    elif dev:
        print(f"  [{OK}] wired")

    # verdict
    print("\n== likely cause")
    if not verdicts:
        print("  The network path looks clean from here. If the eyes still look out of step:")
        print("   - run with --stats on the follower and check it holds ~60 fps")
        print("   - compare the projectors' picture settings (see docs/OPERATIONS.md)")
    if "powersave" in verdicts:
        print("  * Wi-Fi power saving — turn it off (docs/OPERATIONS.md, 'Eyes out of sync')")
    if "bunching" in verdicts and "powersave" not in verdicts:
        if ping_p is not None and "net" not in verdicts:
            print("  * Messages are bunched but the network itself is fine → Nagle's algorithm on the "
                  "broker. Add  set_tcp_nodelay true  to Mosquitto (docs/OPERATIONS.md)")
        else:
            print("  * Messages are bunched and the network is jittery → Wi-Fi (power save, signal, "
                  "or a busy channel)")
    if "net" in verdicts and "powersave" not in verdicts:
        print("  * Slow/jittery network to the broker — Wi-Fi signal or congestion; Ethernet "
              "fixes it outright")
    if "mqtt-slow" in verdicts and "net" not in verdicts:
        print("  * Network is fast but the broker is slow → the Home Assistant host is busy, or "
              "Nagle on the broker (set_tcp_nodelay true)")
    if "brain-rate" in verdicts:
        print("  * The brain isn't holding frame rate — on the brain: sudo systemctl stop demoneye, then")
        print("    ./venv/bin/python demoneye.py --role brain --stats   (Ctrl-C, then start the service)")


if __name__ == "__main__":
    main()
