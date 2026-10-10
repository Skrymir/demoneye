#!/usr/bin/env python3
"""
Show status for Home Assistant, over MQTT discovery.

Each eye process and the tracking bridge publish a small retained JSON status
message plus Home Assistant MQTT-discovery configs, so their entities appear in
HA on their own (grouped under one device per node) with nothing to install.

    halloween/eyes/status/<node>/availability   online | offline   (retained, LWT)
    halloween/eyes/status/<node>/state          JSON snapshot      (retained)
    halloween/eyes/status/bridge/last_seen      ISO timestamp      (retained)
    homeassistant/<component>/demoneye_<node>_<thing>/config       (retained)

<node> is "left" / "right" for the eyes (from the hostname unless config.ini
[status] node says otherwise) and "bridge" for ha_bridge.py.

Rate: published on change (checked once a second) plus a slow refresh
(refresh_s, default 30-60 s). Never at frame rate: the 60 Hz gaze stream stays
on its own topic.

A crash or power loss is caught by the MQTT last will (offline, retained) within
about 1.5x the 30 s keepalive; a clean stop publishes offline straight away.

This module has no pygame / GL / websockets dependency, so it is imported by
both demoneye.py and ha_bridge.py and unit-tested on its own.
"""

import json
import re
import socket
import threading
import time

VERSION = "2026.10.06"

STATUS_DEFAULTS = {
    "enabled": "true",
    "node": "auto",
    "base_topic": "halloween/eyes/status",
    "discovery_prefix": "homeassistant",
    "refresh_s": "30",
    "projector_poll_s": "5",
}

# Show states an eye can report (also the enum options HA is told about).
SHOW_STATES = ["playing", "idle", "waiting_for_display", "lost_brain"]

# The same words demoneye / ha_bridge accept on the command topic.
ON_WORDS = ["on", "1", "true", "play", "start", "yes", "open"]
OFF_WORDS = ["off", "0", "false", "stop", "pause", "no", "close"]


# ---------------------------------------------------------------- pure helpers
def hostname():
    return socket.gethostname().split(".")[0]


def resolve_node(node, host=None):
    """'auto' -> left / right from the hostname (pi-eye-left -> left), else the
    short hostname. Anything else is used as given (sanitised for a topic)."""
    node = (node or "auto").strip().lower()
    if node == "auto":
        h = (host or hostname()).split(".")[0].lower()
        for side in ("left", "right"):
            if side in h:
                return side
        node = h
    return re.sub(r"[^a-z0-9_]+", "_", node).strip("_") or "eye"


def show_state(active, display_up, stale):
    """What the audience sees, in one word."""
    if not active:
        return "idle"
    if not display_up:
        return "waiting_for_display"
    if stale:
        return "lost_brain"
    return "playing"


def projector_word(state):
    """ir_control.projector_state() (True / False / None) -> on / off / unknown."""
    return "on" if state is True else "off" if state is False else "unknown"


def utc_iso(ts=None):
    t = time.gmtime(time.time() if ts is None else ts)
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", t)


def topics(base, node):
    base = base.rstrip("/")
    return {"availability": f"{base}/{node}/availability",
            "state": f"{base}/{node}/state",
            "last_seen": f"{base}/{node}/last_seen"}


# ---------------------------------------------------------- discovery configs
def _device(node, name, model):
    return {"identifiers": [f"demoneye_{node}"], "name": name,
            "manufacturer": "demoneye", "model": model, "sw_version": VERSION}


def _entity(prefix, component, uid, cfg):
    # default_entity_id (not the deprecated object_id) picks the entity_id on
    # first discovery; after that HA's entity registry owns it.
    return f"{prefix}/{component}/{uid}/config", dict(cfg, unique_id=uid)


def eye_discovery(node, base="halloween/eyes/status", prefix="homeassistant",
                  command_topic=None):
    """Discovery configs for one eye. With command_topic (the brain passes it)
    also a 'Show' switch that publishes on/off there, like Node-RED does."""
    t = topics(base, node)
    label = node.capitalize()
    dev = _device(node, f"Demon Eye {label}", "Raspberry Pi 5 eye")
    avail = {"availability_topic": t["availability"]}
    out = dict([
        _entity(prefix, "binary_sensor", f"demoneye_{node}_running", {
            "name": "Running", "device_class": "running",
            "state_topic": t["availability"], "payload_on": "online", "payload_off": "offline",
            "default_entity_id": f"binary_sensor.eye_{node}_running", "device": dev,
        }),
        _entity(prefix, "binary_sensor", f"demoneye_{node}_projector", {
            "name": "Projector", "device_class": "power", **avail,
            "state_topic": t["state"],
            "value_template": "{{ {'on': 'ON', 'off': 'OFF'}.get(value_json.projector, 'None') }}",
            "default_entity_id": f"binary_sensor.projector_{node}", "device": dev,
        }),
        _entity(prefix, "sensor", f"demoneye_{node}_show_state", {
            "name": "Show", "device_class": "enum", "options": SHOW_STATES, **avail,
            "state_topic": t["state"], "value_template": "{{ value_json.show }}",
            "icon": "mdi:eye", "default_entity_id": f"sensor.eyes_{node}_show_state",
            "device": dev,
        }),
        _entity(prefix, "sensor", f"demoneye_{node}_role", {
            "name": "Role", **avail, "state_topic": t["state"],
            "value_template": "{{ value_json.role }}",
            "json_attributes_topic": t["state"],
            "json_attributes_template":
                "{{ {'eye': value_json.eye, 'display': value_json.display,"
                " 'version': value_json.version, 'host': value_json.host} | tojson }}",
            "entity_category": "diagnostic", "icon": "mdi:account-tie-hat",
            "default_entity_id": f"sensor.eyes_{node}_role", "device": dev,
        }),
    ])
    if command_topic:
        on_list, off_list = json.dumps(ON_WORDS), json.dumps(OFF_WORDS)
        out.update([_entity(prefix, "switch", "demoneye_show", {
            "name": "Show", "icon": "mdi:halloween",
            "command_topic": command_topic, "state_topic": command_topic,
            "payload_on": "on", "payload_off": "off", "state_on": "on", "state_off": "off",
            # Node-RED sends plain on/off; the eyes also accept {"state": "ON"} etc.
            "value_template":
                "{% set v = value | lower | trim %}"
                "{% if v.startswith('{') %}{% set v = (value_json.state | default('') | string | lower) %}{% endif %}"
                f"{{{{ 'on' if v in {on_list} else 'off' if v in {off_list} else v }}}}",
            "retain": True, "qos": 1,
            "default_entity_id": "switch.halloween_eyes_show",
            "device": {"identifiers": ["demoneye_show"], "name": "Halloween Eyes",
                       "manufacturer": "demoneye", "model": "Show control",
                       "sw_version": VERSION},
        })])
    return out


def bridge_discovery(node="bridge", base="halloween/eyes/status", prefix="homeassistant"):
    t = topics(base, node)
    dev = _device(node, "Demon Eye Tracking Bridge", "mmWave tracking bridge")
    avail = {"availability_topic": t["availability"]}
    return dict([
        _entity(prefix, "binary_sensor", f"demoneye_{node}_running", {
            "name": "Running", "device_class": "running",
            "state_topic": t["availability"], "payload_on": "online", "payload_off": "offline",
            "default_entity_id": "binary_sensor.eye_bridge_running", "device": dev,
        }),
        _entity(prefix, "binary_sensor", f"demoneye_{node}_tracking", {
            "name": "Tracking", "device_class": "occupancy", **avail,
            "state_topic": t["state"],
            "value_template": "{{ 'ON' if value_json.tracking else 'OFF' }}",
            "default_entity_id": "binary_sensor.tracking_active", "device": dev,
        }),
        # No availability: the last sighting stays readable while the bridge is down.
        _entity(prefix, "sensor", f"demoneye_{node}_last_target_seen", {
            "name": "Last target seen", "device_class": "timestamp",
            "state_topic": t["last_seen"], "icon": "mdi:account-eye",
            "default_entity_id": "sensor.last_target_seen", "device": dev,
        }),
        _entity(prefix, "sensor", f"demoneye_{node}_host", {
            "name": "Host", **avail, "state_topic": t["state"],
            "value_template": "{{ value_json.host }}",
            "json_attributes_topic": t["state"],
            "json_attributes_template":
                "{{ {'enabled': value_json.enabled, 'version': value_json.version} | tojson }}",
            "entity_category": "diagnostic", "icon": "mdi:raspberry-pi",
            "default_entity_id": "sensor.bridge_host", "device": dev,
        }),
        _entity(prefix, "binary_sensor", f"demoneye_{node}_ha_connected", {
            "name": "Home Assistant link", "device_class": "connectivity", **avail,
            "state_topic": t["state"],
            "value_template": "{{ 'ON' if value_json.ha_connected else 'OFF' }}",
            "entity_category": "diagnostic",
            "default_entity_id": "binary_sensor.eye_bridge_ha_connected", "device": dev,
        }),
    ])


class CachedProbe:
    """Calls fn() at most every interval seconds; returns the cached value in
    between. Used for the HDMI read so the status thread (1 Hz) doesn't hit
    sysfs and /proc/cmdline every tick."""

    def __init__(self, fn, interval, clock=time.monotonic):
        self.fn, self.interval, self.clock = fn, max(0.5, float(interval)), clock
        self._at, self._val = None, None

    def __call__(self):
        now = self.clock()
        if self._at is None or now - self._at >= self.interval:
            try:
                self._val = self.fn()
            except Exception:
                self._val = None
            self._at = now
        return self._val


# ------------------------------------------------------------------ publisher
class StatusPublisher:
    """Owns one node's status on an existing paho client.

    Call it in this order:
        sp = StatusPublisher(client, node, discovery, snapshot_fn, ...)   # sets the LWT
        client.connect(...); client.loop_start()
        in on_connect:  sp.on_connect()
        sp.start()                     # background thread: on change + refresh
        on clean exit:  sp.go_offline()  (before loop_stop / disconnect)
    """

    def __init__(self, client, node, discovery, snapshot_fn, base="halloween/eyes/status",
                 prefix="homeassistant", refresh_s=30.0, tick_s=1.0, clock=time.monotonic):
        self.client = client
        self.node = node
        self.discovery = discovery
        self.snapshot_fn = snapshot_fn
        self.topics = topics(base, node)
        self.prefix = prefix
        self.refresh_s = max(5.0, float(refresh_s))
        self.tick_s = tick_s
        self.clock = clock
        self._last = None
        self._last_pub = None
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        # LWT: must be set before connect(). Retained, so HA sees "offline"
        # even if it (re)starts after the crash.
        client.will_set(self.topics["availability"], "offline", qos=1, retain=True)
        # HA announces itself on <prefix>/status when it starts: resend then, in
        # case the retained configs were cleared from the broker.
        client.message_callback_add(f"{prefix}/status", self._on_ha_status)

    # -- MQTT events --
    def on_connect(self):
        """Call from the client's on_connect (also runs after a reconnect)."""
        self.client.subscribe(f"{self.prefix}/status", qos=1)
        self.publish_discovery()
        self.client.publish(self.topics["availability"], "online", qos=1, retain=True)
        self.tick(force=True)

    def _on_ha_status(self, client, userdata, msg):
        if msg.payload.decode(errors="ignore").strip().lower() == "online":
            self.publish_discovery()
            self.client.publish(self.topics["availability"], "online", qos=1, retain=True)
            self.tick(force=True)

    def publish_discovery(self):
        for topic, cfg in self.discovery.items():
            self.client.publish(topic, json.dumps(cfg, separators=(",", ":")), qos=1, retain=True)

    # -- state --
    def tick(self, force=False):
        """Publish the snapshot if it changed or the refresh is due. Returns
        True if it published."""
        try:
            snap = self.snapshot_fn()
        except Exception as e:                      # never take the show down over status
            print(f"[status] snapshot failed: {e}", flush=True)
            return False
        now = self.clock()
        with self._lock:
            due = (force or snap != self._last or self._last_pub is None
                   or now - self._last_pub >= self.refresh_s)
            if not due:
                return False
        payload = json.dumps(snap, sort_keys=True)   # may raise: then nothing is marked sent
        self.client.publish(self.topics["state"], payload, qos=1, retain=True)
        with self._lock:
            self._last, self._last_pub = snap, now
        return True

    def publish_last_seen(self, ts=None):
        self.client.publish(self.topics["last_seen"], utc_iso(ts), qos=1, retain=True)

    # -- lifecycle --
    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name=f"status-{self.node}", daemon=True)
        self._thread.start()

    def _run(self):
        # Nothing may end this loop but go_offline(): a dead status thread
        # leaves HA showing frozen state that still says "online".
        last_err, last_err_at = None, -1e9
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception as e:
                now = self.clock()
                if repr(e) != last_err or now - last_err_at > 60:
                    print(f"[status] publish failed: {e!r} — will keep trying", flush=True)
                    last_err, last_err_at = repr(e), now

    def go_offline(self, timeout=1.0):
        """Clean shutdown: say so now rather than waiting for the LWT."""
        self._stop.set()
        try:
            info = self.client.publish(self.topics["availability"], "offline", qos=1, retain=True)
            info.wait_for_publish(timeout=timeout)
        except Exception:
            pass


def load_section(cp, section="status", overrides=None):
    """Read a [status] section (ConfigParser) over STATUS_DEFAULTS. Returns a
    plain dict of typed values."""
    vals = dict(STATUS_DEFAULTS, **(overrides or {}))
    if cp is not None and cp.has_section(section):
        vals.update({k: v for k, v in cp.items(section)})

    def _b(v):
        return str(v).strip().lower() in ("1", "yes", "true", "on")
    return {
        "enabled": _b(vals["enabled"]),
        "node": vals["node"].strip(),
        "base_topic": vals["base_topic"].strip().rstrip("/"),
        "discovery_prefix": vals["discovery_prefix"].strip().rstrip("/"),
        "refresh_s": float(vals["refresh_s"]),
        "projector_poll_s": float(vals["projector_poll_s"]),
    }
