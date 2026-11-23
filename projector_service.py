#!/usr/bin/env python3
"""
Projector power service: the projector half of demoneye, without the eye.

For seasons the window Pis are booted into FPP (Christmas). It follows ONE
retained show command, exactly like demoneye follows house/show/command:

    house/christmas/command     on / off (retained, QoS 1; also 1/0, play/stop,
                                {"state": "ON"} — the same words demoneye takes)

On -> projector on, off -> projector off, each confirmed by HDMI (ir_control:
no press if it's already there, one retry if a press was missed). The command
is retained, so a Pi that reboots mid-evening picks up the current state. A
repeat of the command already carried out (an MQTT reconnect replays the
retained one) is ignored, unless the last attempt failed.

Status for Home Assistant uses demoneye's own entity: binary_sensor.projector_left
/ _right on the "Demon Eye Left/Right" device, so dashboards and alerts work
the same in both seasons. Here its state comes from this service:

    house/status/projector_<node>/availability   online | offline (LWT)
    house/status/projector_<node>/state          JSON:
        power     on | off | unknown        HDMI state right now
        result    idle | turning_on | on | turning_off | off |
                  failed_on | failed_off   outcome of the last command
        desired   on | off | none           command being followed
        changed   ISO time result last changed
    (result/desired/changed show up as attributes of the projector entity)

When the Pi boots back into demoneye, demoneye re-publishes the entity's
discovery and takes it back.

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
    "command_topic": "house/christmas/command",
    "node": "auto",
}

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
        "status": status,
        "projector": cp["projector"] if cp.has_section("projector") else None,
    }


def discovery(side, base="house/status", prefix="homeassistant"):
    """HA discovery for demoneye's projector entity (same unique_id, entity_id
    and device as ha_status.eye_discovery), fed by this service's topics."""
    t = ha_status.topics(base, f"projector_{side}")
    uid = f"demoneye_{side}_projector"
    return {f"{prefix}/binary_sensor/{uid}/config": {
        "unique_id": uid, "name": "Projector", "device_class": "power",
        "availability_topic": t["availability"],
        "state_topic": t["state"],
        "value_template": "{{ {'on': 'ON', 'off': 'OFF'}.get(value_json.power, 'None') }}",
        "json_attributes_topic": t["state"],
        "json_attributes_template":
            "{{ {'result': value_json.result, 'desired': value_json.desired,"
            " 'changed': value_json.changed, 'host': value_json.host,"
            " 'source': 'projector_service'} | tojson }}",
        "default_entity_id": f"binary_sensor.projector_{side}",
        "device": ha_status._device(side, f"Demon Eye {side.capitalize()}",
                                    "Raspberry Pi 5 eye"),
    }}


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
        """Follow `want`. Returns False if it's a repeat of the command already
        carried out (e.g. the retained command replayed on an MQTT reconnect):
        acting again could toggle a projector whose HDMI state can't be read."""
        with self._lock:
            pending = self._wake.is_set()
            repeat = (want == self.desired and not pending
                      and not self.result.startswith("failed"))
            if repeat:
                return False
            self.desired = want
        self._wake.set()
        return True

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
        client, node, discovery(side, st["base_topic"], st["discovery_prefix"]),
        snapshot, base=st["base_topic"], prefix=st["discovery_prefix"],
        refresh_s=st["refresh_s"])

    def on_connect(client, userdata, flags, rc, properties=None):
        failed = rc.is_failure if hasattr(rc, "is_failure") else rc != 0
        if failed:
            print(f"[mqtt] broker refused the connection: {rc}", flush=True)
            return
        print(f"[mqtt] connected to {cfg['host']}:{cfg['port']}", flush=True)
        client.subscribe(cfg["command_topic"], 1)
        status.on_connect()

    def on_message(client, userdata, msg):
        if msg.topic != cfg["command_topic"]:
            return
        want = _parse_command(msg.payload)
        if want is None:
            print(f"[cmd] ignoring {msg.payload[:40]!r} on {msg.topic}", flush=True)
            return
        word = "on" if want else "off"
        how = " (retained)" if msg.retain else ""
        if worker.command(want):
            print(f"[cmd] show {word}{how}", flush=True)
        else:
            print(f"[cmd] show {word}{how}: already done, nothing to do", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(cfg["host"], cfg["port"], keepalive=30)
    client.loop_start()
    worker.start()
    status.start()
    print(f"[projector] '{side}' following {cfg['command_topic']}; "
          f"status on {st['base_topic']}/{node}/", flush=True)

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
