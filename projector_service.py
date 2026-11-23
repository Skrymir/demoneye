#!/usr/bin/env python3
"""
Projector power service: turn the window projectors on/off over MQTT, with the
HDMI check from ir_control.py, and nothing else (no eye, no display, no GL).

Use this when a Pi is booted into something other than demoneye (FPP for the
Christmas show) but Home Assistant should still own projector power. Only
needs paho-mqtt + ir-ctl; imports ir_control.py and ha_status.py unchanged.

Commands (payloads like demoneye's: on/off/1/0/true/false/start/stop or
{"state": "on"}):
    house/projector/command            both projectors
    house/projector/<node>/command     just this one (node = left / right)

Status (retained; Home Assistant entities appear by MQTT discovery under a
"Window Projector Left/Right" device):
    house/status/projector_<node>/availability   online | offline (LWT)
    house/status/projector_<node>/state          JSON:
        power     on | off | unknown        HDMI state right now
        result    idle | turning_on | on | turning_off | off |
                  failed_on | failed_off   outcome of the last command
        desired   on | off | none           last command received
        changed   ISO time result last changed

Node-RED verifies a command by waiting for result == "on" / "off" on both
projectors; "failed_on" / "failed_off" means ir_control gave up after its
retries (the projector didn't change HDMI state).

Run:
    python projector_service.py                 # uses config.ini next to it
    python projector_service.py --config /path/to/config.ini
"""

import argparse
import configparser
import json
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

import ha_status
import ir_control
from directors import _parse_command

DEFAULTS = {
    "command_topic": "house/projector/command",
    "node_command_base": "house/projector",
    "node": "auto",
}

RESULTS = ["idle", "turning_on", "on", "turning_off", "off", "failed_on", "failed_off"]


# ---------------------------------------------------------------- config
def load(path):
    cp = configparser.ConfigParser()
    if not cp.read(path):
        print(f"[projector] {path} not found — using defaults", flush=True)
    m = cp["mqtt"] if cp.has_section("mqtt") else {}
    ps = dict(DEFAULTS)
    if cp.has_section("projector_service"):
        ps.update({k: v.strip() for k, v in cp.items("projector_service")})
    status = ha_status.load_section(cp)
    side = ha_status.resolve_node(ps["node"])
    return {
        "host": m.get("host", "192.168.86.5"),
        "port": int(m.get("port", 1883)),
        "user": os.environ.get("MQTT_USER") or m.get("username", ""),
        "password": os.environ.get("MQTT_PASS") or m.get("password", ""),
        "side": side,
        "command_topic": ps["command_topic"],
        "node_command_topic": f"{ps['node_command_base'].rstrip('/')}/{side}/command",
        "status": status,
        "projector": cp["projector"] if cp.has_section("projector") else None,
    }


def discovery(side, command_topic, base="house/status", prefix="homeassistant"):
    """HA discovery: a Projector switch (state from HDMI, commands to this
    projector's own topic) and a Last result sensor."""
    node = f"projector_{side}"
    t = ha_status.topics(base, node)
    dev = {"identifiers": [f"demoneye_{node}"], "name": f"Window Projector {side.capitalize()}",
           "manufacturer": "demoneye", "model": "IR projector control",
           "sw_version": ha_status.VERSION}
    avail = {"availability_topic": t["availability"]}

    def ent(component, thing, cfg):
        uid = f"demoneye_{node}_{thing}"
        return f"{prefix}/{component}/{uid}/config", dict(cfg, unique_id=uid, device=dev)

    return dict([
        ent("switch", "power", {
            "name": "Projector", "icon": "mdi:projector", **avail,
            "command_topic": command_topic, "payload_on": "on", "payload_off": "off",
            "state_topic": t["state"],
            "value_template": "{{ {'on': 'on', 'off': 'off'}.get(value_json.power, 'None') }}",
            "state_on": "on", "state_off": "off",
            "default_entity_id": f"switch.window_projector_{side}",
        }),
        ent("sensor", "result", {
            "name": "Last result", "device_class": "enum", "options": RESULTS, **avail,
            "state_topic": t["state"], "value_template": "{{ value_json.result }}",
            "default_entity_id": f"sensor.window_projector_{side}_result",
        }),
        ent("binary_sensor", "running", {
            "name": "Running", "device_class": "running",
            "state_topic": t["availability"], "payload_on": "online", "payload_off": "offline",
            "default_entity_id": f"binary_sensor.window_projector_{side}_running",
        }),
    ])


# ---------------------------------------------------------------- worker
class PowerWorker:
    """Drives the projector toward the LATEST command on one thread. An "off"
    that arrives during a slow "on" runs right after it; a burst of commands
    collapses to the last one, so the toggle can't get out of step."""

    def __init__(self, power_on, power_off, on_change, clock=time.time):
        self.power_on, self.power_off, self.on_change, self.clock = power_on, power_off, on_change, clock
        self.desired = None
        self.result = "idle"
        self.changed = clock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def command(self, want):
        with self._lock:
            self.desired = want
        self._wake.set()

    def _set(self, result):
        with self._lock:
            self.result, self.changed = result, self.clock()
        self.on_change()

    def run_once(self):
        """Handle the pending command, if any. Returns True if it did work."""
        if not self._wake.is_set():
            return False
        self._wake.clear()
        with self._lock:
            want = self.desired
        if want is None:
            return False
        word = "on" if want else "off"
        self._set(f"turning_{word}")
        ok = (self.power_on if want else self.power_off)()
        with self._lock:
            superseded = self._wake.is_set() and self.desired != want
        if not superseded:
            self._set(word if ok else f"failed_{word}")
        return True

    def start(self):
        self._thread = threading.Thread(target=self._run, name="projector-power", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(1.0)
            if self._stop.is_set():          # stopping: never press IR on the way out
                break
            try:
                self.run_once()
            except Exception as e:          # never let the worker die
                print(f"[projector] power command crashed: {e!r}", flush=True)
                self._set("failed_on" if self.desired else "failed_off")

    def stop(self):
        self._stop.set()
        self._wake.set()


# ---------------------------------------------------------------- service
def make_client(cid):
    # paho 2.x needs the callback API version; FPP's apt package may be 1.6.
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    return mqtt.Client(client_id=cid)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Projector power over MQTT")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--config", default=os.path.join(here, "config.ini"))
    args = ap.parse_args(argv)
    cfg = load(args.config)

    if cfg["projector"] is not None:
        ir_control.configure(cfg["projector"])
    if not ir_control.available():
        print("[projector] ir-ctl is not installed (sudo apt install v4l-utils) — "
              "commands will report failed", flush=True)

    side, st = cfg["side"], cfg["status"]
    node = f"projector_{side}"
    probe = ha_status.CachedProbe(ir_control.projector_state, st["projector_poll_s"])
    client = make_client(f"projector-{side}-{os.getpid()}")
    if cfg["user"]:
        client.username_pw_set(cfg["user"], cfg["password"])

    status = None
    worker = None

    def snapshot():
        return {"power": ha_status.projector_word(probe()), "result": worker.result,
                "desired": {True: "on", False: "off", None: "none"}[worker.desired],
                "changed": ha_status.utc_iso(worker.changed),
                "host": ha_status.hostname(), "version": ha_status.VERSION}

    def changed():
        probe._at = None                    # re-read HDMI now, not on the next poll
        if status:
            status.tick(force=True)

    worker = PowerWorker(ir_control.power_on, ir_control.power_off, changed)
    status = ha_status.StatusPublisher(
        client, node, discovery(side, cfg["node_command_topic"], st["base_topic"],
                                st["discovery_prefix"]),
        snapshot, base=st["base_topic"], prefix=st["discovery_prefix"],
        refresh_s=st["refresh_s"])

    def on_connect(client, userdata, flags, rc, properties=None):
        failed = rc.is_failure if hasattr(rc, "is_failure") else rc != 0
        if failed:
            print(f"[mqtt] broker refused the connection: {rc}", flush=True)
            return
        print(f"[mqtt] connected to {cfg['host']}:{cfg['port']}", flush=True)
        client.subscribe([(cfg["command_topic"], 1), (cfg["node_command_topic"], 1)])
        status.on_connect()

    def on_message(client, userdata, msg):
        if msg.topic not in (cfg["command_topic"], cfg["node_command_topic"]):
            return
        if msg.retain:
            # A retained command is old news (left on the broker by someone);
            # acting on it at every boot would toggle projectors unasked.
            print(f"[cmd] ignoring retained message on {msg.topic}", flush=True)
            return
        want = _parse_command(msg.payload)
        if want is None:
            print(f"[cmd] ignoring {msg.payload[:40]!r} on {msg.topic}", flush=True)
            return
        print(f"[cmd] {'on' if want else 'off'} ({msg.topic})", flush=True)
        worker.command(want)

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(cfg["host"], cfg["port"], keepalive=30)
    client.loop_start()
    worker.start()
    status.start()
    print(f"[projector] '{side}' listening on {cfg['command_topic']} and "
          f"{cfg['node_command_topic']}; status on {st['base_topic']}/{node}/", flush=True)

    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    done.wait()
    print("[projector] stopping", flush=True)
    worker.stop()
    status.go_offline()
    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
