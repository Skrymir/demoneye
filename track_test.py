#!/usr/bin/env python3
"""
Tracking test rig — exercise the eyes' target system without a sensor.

Publishes simulated targets to the eye's target topic so you can verify the
whole tracking path (target -> brain Director -> synced eyes) before the mmWave
sensor is mounted. Also has a monitor mode that just watches the traffic.

Usage:
  python track_test.py sweep            # walk a target left->right, repeatedly
  python track_test.py sweep --speed 4  # faster walk-by
  python track_test.py approach         # walk toward the house (y rises), then leave
  python track_test.py point -0.8 0.2   # hold one spot (then Ctrl-C)
  python track_test.py clear            # release any target
  python track_test.py interactive      # arrow keys / numbers to drive it live
  python track_test.py monitor          # watch state+target+command traffic

  --host / --port / --topic / --state-topic override config.ini values.
"""

import argparse
import configparser
import json
import math
import os
import sys
import time

import paho.mqtt.client as mqtt


def load_cfg(path):
    cp = configparser.ConfigParser()
    cp.read_dict({"mqtt": {
        "host": "127.0.0.1", "port": "1883", "username": "", "password": "",
        "topic": "halloween/eyes/state",
        "command_topic": "halloween/eyes/command",
        "target_topic": "halloween/eyes/target",
    }})
    if path and os.path.exists(path):
        cp.read(path)
        print(f"[cfg] loaded {path}")
    else:
        print(f"[cfg] {path} not found — using defaults")
    return cp


def connect(cfg, args, on_message=None, subscribe=()):
    host = args.host or cfg.get("mqtt", "host")
    port = args.port or cfg.getint("mqtt", "port")
    user = os.environ.get("MQTT_USER") or cfg.get("mqtt", "username")
    pw = os.environ.get("MQTT_PASS") or cfg.get("mqtt", "password")

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"track-test-{os.getpid()}")
    if user:
        c.username_pw_set(user, pw)
    if on_message:
        c.on_message = on_message

    def on_connect(client, userdata, flags, rc, props):
        print(f"[mqtt] connected to {host}:{port} rc={rc}")
        for t in subscribe:
            client.subscribe(t, qos=0)
            print(f"[mqtt] subscribed {t}")

    c.on_connect = on_connect
    c.connect(host, port, keepalive=30)
    c.loop_start()
    time.sleep(0.4)
    return c


def pub_target(c, topic, x, y, hold=2.0, quiet=False):
    payload = json.dumps({"x": round(x, 3), "y": round(y, 3), "hold": hold})
    c.publish(topic, payload, qos=0)
    if not quiet:
        print(f"  -> {payload}")


# ---------------------------------------------------------------------------
def cmd_sweep(c, topic, args):
    """Target walks back and forth across the field of view."""
    print(f"Sweeping a target across the view at {args.speed:.1f}s per crossing.")
    print("The eyes should snap to it (surprise), then follow. Ctrl-C to stop.\n")
    hz = 15.0
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            # triangle wave -1 -> +1 -> -1
            phase = (t / args.speed) % 2.0
            tri = phase if phase <= 1.0 else 2.0 - phase   # 0..1..0
            x = tri * 2.0 - 1.0                            # -1 .. +1 .. -1
            y = 0.12 * math.sin(t * 1.7)
            pub_target(c, topic, x, y, args.hold, quiet=True)
            print(f"\r  target x={x:+.2f} y={y:+.2f}   ", end="", flush=True)
            time.sleep(1.0 / hz)
    except KeyboardInterrupt:
        print("\nreleasing target")
        c.publish(topic, "clear", qos=0)


def cmd_approach(c, topic, args):
    """Target walks toward the house, pauses, then leaves."""
    print("Target approaches, holds, then leaves. Ctrl-C to stop.\n")
    hz = 15.0
    try:
        while True:
            for label, dur, fn in (
                ("approaching", 4.0, lambda p: (-0.7 + 0.7 * p, -0.5 + 0.5 * p)),
                ("at the door", 3.0, lambda p: (0.05 * math.sin(p * 6), 0.0)),
                ("leaving",     3.0, lambda p: (0.3 * p, -0.2 * p)),
            ):
                print(f"\n[{label}]")
                n = int(dur * hz)
                for i in range(n):
                    x, y = fn(i / n)
                    pub_target(c, topic, x, y, args.hold, quiet=True)
                    print(f"\r  x={x:+.2f} y={y:+.2f}   ", end="", flush=True)
                    time.sleep(1.0 / hz)
            print("\n[gone] clearing target — eyes should drift back to idle")
            c.publish(topic, "clear", qos=0)
            time.sleep(4.0)
    except KeyboardInterrupt:
        print("\nreleasing target")
        c.publish(topic, "clear", qos=0)


def cmd_point(c, topic, args):
    """Hold a fixed target; re-sends so it doesn't time out."""
    print(f"Holding target at x={args.x:+.2f} y={args.y:+.2f}. Ctrl-C to release.")
    print("(re-sending every 0.75s — this is what the bridge's heartbeat does)\n")
    try:
        while True:
            pub_target(c, topic, args.x, args.y, args.hold)
            time.sleep(0.75)
    except KeyboardInterrupt:
        print("\nreleasing target")
        c.publish(topic, "clear", qos=0)


def cmd_clear(c, topic, args):
    c.publish(topic, "clear", qos=0)
    print("cleared — eyes should return to idle wandering")
    time.sleep(0.3)


def cmd_interactive(c, topic, args):
    """Drive the target from the keyboard."""
    print("Interactive target. Type a command then Enter:")
    print("  a/d = move left/right    w/s = up/down    c = clear")
    print("  0-9 = jump to a position across the view (0=far left, 9=far right)")
    print("  q   = quit\n")
    x, y = 0.0, 0.0
    import threading
    stop = threading.Event()

    def heartbeat():
        while not stop.is_set():
            if held[0]:
                pub_target(c, topic, x, y, args.hold, quiet=True)
            time.sleep(0.5)

    held = [True]
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        while True:
            s = input(f"[x={x:+.2f} y={y:+.2f}] > ").strip().lower()
            if s in ("q", "quit", "exit"):
                break
            for ch in s:
                if ch == "a":
                    x = max(-1.0, x - 0.2)
                elif ch == "d":
                    x = min(1.0, x + 0.2)
                elif ch == "w":
                    y = min(1.0, y + 0.2)
                elif ch == "s":
                    y = max(-1.0, y - 0.2)
                elif ch == "c":
                    held[0] = False
                    c.publish(topic, "clear", qos=0)
                    print("  cleared")
                elif ch.isdigit():
                    x = (int(ch) / 4.5) - 1.0
                    held[0] = True
            if held[0] and s and s[-1] != "c":
                pub_target(c, topic, x, y, args.hold)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop.set()
        c.publish(topic, "clear", qos=0)
        print("\nreleased")


def cmd_monitor(c, topic, args):
    """Just watch the traffic (subscriptions set up in main)."""
    print("Watching traffic. Ctrl-C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\ndone")


def main():
    default_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    ap = argparse.ArgumentParser(description="Eye tracking test rig")
    ap.add_argument("mode", choices=["sweep", "approach", "point", "clear",
                                     "interactive", "monitor"])
    ap.add_argument("x", nargs="?", type=float, default=0.0, help="point mode x")
    ap.add_argument("y", nargs="?", type=float, default=0.0, help="point mode y")
    ap.add_argument("--config", default=default_cfg)
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--topic", help="target topic override")
    ap.add_argument("--state-topic", help="state topic override (monitor)")
    ap.add_argument("--speed", type=float, default=6.0, help="seconds per crossing (sweep)")
    ap.add_argument("--hold", type=float, default=2.0, help="hold seconds sent with targets")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    topic = args.topic or cfg.get("mqtt", "target_topic")
    state_topic = args.state_topic or cfg.get("mqtt", "topic")
    cmd_topic = cfg.get("mqtt", "command_topic")

    subs = ()
    on_msg = None
    if args.mode == "monitor":
        last = [0.0]

        def on_message(client, userdata, msg):
            now = time.time()
            if msg.topic == state_topic:
                if now - last[0] < 0.5:     # state streams at 50Hz; sample it
                    return
                last[0] = now
                try:
                    d = json.loads(msg.payload)
                    print(f"[state] gaze=({d.get('gx',0):+.2f},{d.get('gy',0):+.2f}) "
                          f"blink={d.get('blink',0):.2f} dil={d.get('dilation',0):.2f} "
                          f"open={d.get('open',1):.2f} slant={d.get('slant',0):+.2f}")
                except Exception:
                    pass
            else:
                tag = "target" if msg.topic == topic else "cmd"
                print(f"[{tag}] {msg.payload.decode(errors='ignore')[:90]}")

        on_msg = on_message
        subs = (state_topic, topic, cmd_topic)

    c = connect(cfg, args, on_message=on_msg, subscribe=subs)
    try:
        {"sweep": cmd_sweep, "approach": cmd_approach, "point": cmd_point,
         "clear": cmd_clear, "interactive": cmd_interactive,
         "monitor": cmd_monitor}[args.mode](c, topic, args)
    finally:
        time.sleep(0.3)
        c.loop_stop()
        c.disconnect()


if __name__ == "__main__":
    main()
