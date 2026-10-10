#!/usr/bin/env python3
"""
Home Assistant -> eye target bridge.

Connects to Home Assistant over its WebSocket API, watches an Everything Presence
Pro's mmWave target coordinates (LD2450, up to 3 targets, X/Y in millimetres),
picks the nearest active target, normalizes its lateral position to the eye's
gaze range (-1..+1), and publishes it to the eye's MQTT target topic. The brain
then snaps the eyes to it and follows. When no target is present it releases and
the eyes drift back to idle.

Run:  python ha_bridge.py   (reads bridge.ini next to it; HA_TOKEN env overrides)

This runs anywhere that can reach both Home Assistant and your MQTT broker — the
brain Pi is a fine home for it. Run it on ONE machine only.

On/off: with follow_command = true (the default) it obeys the same command topic
as the eyes. While the show is off it publishes no targets and doesn't even
hold a connection to Home Assistant; "on" connects, seeds and starts tracking.

Status: it also publishes its own low-rate, retained status for Home Assistant
(running, tracking, last target seen, host, HA link) via MQTT discovery — see
ha_status.py and bridge.ini [status]. That keeps going while the show is off,
so HA can tell "off" from "dead".
"""

import asyncio
import configparser
import json
import math
import os
import signal
import socket
import sys
import time

import websockets
import paho.mqtt.client as mqtt

import ha_status


# ---------------------------------------------------------------------------
def _f(v):
    """Parse a HA state string to float, or None if unknown/unavailable/blank."""
    try:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s in ("", "unknown", "unavailable", "none"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _truthy(v):
    return str(v).strip().lower() in ("on", "true", "1", "home", "detected", "occupied", "yes")


class Config:
    def __init__(self, path):
        cp = configparser.ConfigParser()
        cp.read_dict({
            "home_assistant": {"url": "ws://homeassistant.local:8123/api/websocket", "token": ""},
            "entities": {
                "target_count": "3",
                "x_pattern": "sensor.s1_pro_multi_sense_ae8394_target_{n}_x",
                "y_pattern": "sensor.s1_pro_multi_sense_ae8394_target_{n}_y",
                "active_pattern": "",
            },
            "mqtt": {"host": "192.168.86.5", "port": "1883", "username": "", "password": "",
                     "target_topic": "halloween/eyes/target",
                     "command_topic": "halloween/eyes/command",
                     "follow_command": "true", "start_enabled": "false"},
            "calibration": {
                "x_range_mm": "250", "x_offset_mm": "0", "invert_x": "false",
                "y_from_distance": "false", "y_near_mm": "500", "y_far_mm": "4000",
                "smooth_factor": "0.6", "publish_hz": "20", "heartbeat_s": "0.75",
                "hold_s": "2.0",
                "geometry": "false", "sensor_unit_mm": "1",
                "sensor_x_mm": "0", "sensor_y_mm": "0", "sensor_yaw_deg": "0",
                "sensor_flip_x": "false", "gaze_max_deg": "60",
            },
            "status": dict(ha_status.STATUS_DEFAULTS, node="bridge", refresh_s="60",
                           last_seen_every_s="10"),
        })
        if path and os.path.exists(path):
            cp.read(path)
            print(f"[cfg] loaded {path}", flush=True)
        else:
            print(f"[cfg] {path} not found — using defaults", flush=True)

        self.url = cp.get("home_assistant", "url")
        self.token = os.environ.get("HA_TOKEN") or cp.get("home_assistant", "token")

        n = cp.getint("entities", "target_count")
        xp = cp.get("entities", "x_pattern")
        yp = cp.get("entities", "y_pattern")
        ap = cp.get("entities", "active_pattern")
        self.targets = []
        for i in range(1, n + 1):
            self.targets.append({
                "x": xp.format(n=i),
                "y": yp.format(n=i),
                "active": ap.format(n=i) if ap.strip() else None,
            })
        # flat set of every entity we track
        self.entities = set()
        for t in self.targets:
            self.entities.update(v for v in t.values() if v)

        self.mqtt_host = cp.get("mqtt", "host")
        self.mqtt_port = cp.getint("mqtt", "port")
        # env first, like the eyes (settings.py): credentials live in
        # /etc/ha-bridge.env, not in bridge.ini
        self.mqtt_user = os.environ.get("MQTT_USER") or cp.get("mqtt", "username")
        self.mqtt_pass = os.environ.get("MQTT_PASS") or cp.get("mqtt", "password")
        self.topic = cp.get("mqtt", "target_topic")
        self.command_topic = cp.get("mqtt", "command_topic")
        self.follow_command = cp.getboolean("mqtt", "follow_command")
        self.start_enabled = cp.getboolean("mqtt", "start_enabled")

        self.x_range = cp.getfloat("calibration", "x_range_mm")
        self.x_offset = cp.getfloat("calibration", "x_offset_mm")
        self.invert_x = cp.getboolean("calibration", "invert_x")
        self.y_from_distance = cp.getboolean("calibration", "y_from_distance")
        self.y_near = cp.getfloat("calibration", "y_near_mm")
        self.y_far = cp.getfloat("calibration", "y_far_mm")
        # Sensor placement (geometry = true): where the sensor sits relative to
        # the eyes and how it is turned, so targets become real look angles.
        self.geometry = cp.getboolean("calibration", "geometry")
        self.unit_mm = cp.getfloat("calibration", "sensor_unit_mm")
        self.sensor_x = cp.getfloat("calibration", "sensor_x_mm")
        self.sensor_y = cp.getfloat("calibration", "sensor_y_mm")
        self.yaw = math.radians(cp.getfloat("calibration", "sensor_yaw_deg"))
        self.flip_x = cp.getboolean("calibration", "sensor_flip_x")
        self.gaze_max = max(1.0, cp.getfloat("calibration", "gaze_max_deg"))
        self.smooth = min(1.0, max(0.01, cp.getfloat("calibration", "smooth_factor")))
        self.min_interval = 1.0 / max(1.0, cp.getfloat("calibration", "publish_hz"))
        self.heartbeat = cp.getfloat("calibration", "heartbeat_s")
        self.hold = cp.getfloat("calibration", "hold_s")

        st = ha_status.load_section(cp, "status")
        self.status_enabled = st["enabled"]
        self.status_node = ha_status.resolve_node(st["node"])
        self.status_base = st["base_topic"]
        self.status_prefix = st["discovery_prefix"]
        self.status_refresh = st["refresh_s"]
        self.last_seen_every = cp.getfloat("status", "last_seen_every_s")


def _clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


# ---------------------------------------------------------------------------
def set_nodelay(client):
    """No Nagle on the MQTT socket (paho leaves it on): small messages go out
    immediately instead of being held for the previous one's ACK."""
    try:
        sock = client.socket()
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return True
    except (OSError, AttributeError):
        pass
    return False


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cache = {}          # entity_id -> state string
        self.sx = 0.0            # smoothed gaze x
        self.sy = 0.0
        self.have = False        # a target is currently present
        self.last_pub = 0.0
        self.client = None
        # When follow_command is on, the bridge only publishes targets while the
        # show is running — no point streaming radar data all day. It listens on
        # the same command topic the eyes use, so one message controls both.
        self.enabled = True if not cfg.follow_command else cfg.start_enabled
        self._loop = None        # asyncio loop, so the MQTT thread can wake it
        self._on_evt = None
        self._off_evt = None
        self.ha_connected = False  # Home Assistant WebSocket authenticated and up
        self.status = None       # ha_status.StatusPublisher once MQTT is set up
        self._seen_pub = None    # monotonic time last_seen was last published
        self._host = ha_status.hostname()

    def _sync_events(self):
        """Mirror self.enabled into the asyncio events (runs on the loop)."""
        if self._on_evt is None:
            return
        if self.enabled:
            self._off_evt.clear(); self._on_evt.set()
        else:
            self._on_evt.clear(); self._off_evt.set()

    # -- target math (pure, unit-tested) --
    def pick_target(self):
        """Return (x_mm, y_mm) of the nearest present target, or None."""
        best = None
        best_d = 1e18
        for t in self.cfg.targets:
            x = _f(self.cache.get(t["x"]))
            y = _f(self.cache.get(t["y"]))
            if x is None or y is None:
                continue
            act = self.cache.get(t["active"]) if t["active"] else None
            if act is not None and not _truthy(act):
                continue
            if abs(x) < 1.0 and abs(y) < 1.0:      # empty LD2450 slot reads ~0,0
                continue
            d = math.hypot(x, y)
            if d < best_d:
                best_d = d
                best = (x, y)
        return best

    def to_world(self, x, y):
        """Sensor reading -> (X, Y) in mm relative to the eyes.

        World frame, seen from above, standing at the house facing the street:
        origin = the house wall midway between the two projected eyes,
        +X = to your right, +Y = straight out toward the street.
        The sensor sits at (sensor_x_mm, sensor_y_mm) and is turned
        sensor_yaw_deg to the LEFT (counter-clockwise) of straight out.
        Its own frame: x = to its right, y = straight ahead of it.
        """
        c = self.cfg
        u = x * c.unit_mm * (-1.0 if c.flip_x else 1.0)
        v = y * c.unit_mm
        cos_t, sin_t = math.cos(c.yaw), math.sin(c.yaw)
        return (c.sensor_x + u * cos_t - v * sin_t,
                c.sensor_y + u * sin_t + v * cos_t)

    def normalize(self, x_mm, y_mm):
        if self.cfg.geometry:
            wx, wy = self.to_world(x_mm, y_mm)
            # Bearing from the eyes: 0 = straight out, + = toward world +X.
            # The eyes face the street, so their right is the audience's left:
            # a target at +X needs the pupil toward screen-left (negative).
            bearing = math.degrees(math.atan2(wx, max(wy, 1.0)))
            gx = _clamp(-bearing / self.cfg.gaze_max)
            dist = math.hypot(wx, wy)          # mm from the eyes
        else:
            gx = _clamp((x_mm - self.cfg.x_offset) / self.cfg.x_range)
            dist = y_mm
        if self.cfg.invert_x:
            gx = -gx
        if self.cfg.y_from_distance and self.cfg.y_far != self.cfg.y_near:
            # closer target -> eye looks further "down" (-1); far -> level (0)
            frac = (self.cfg.y_far - dist) / (self.cfg.y_far - self.cfg.y_near)
            gy = -_clamp(frac, 0.0, 1.0)
        else:
            gy = 0.0
        return gx, gy

    # -- MQTT --
    ON_WORDS = {"on", "1", "true", "play", "start", "yes", "open"}
    OFF_WORDS = {"off", "0", "false", "stop", "pause", "no", "close"}

    def _on_command(self, client, userdata, msg):
        """Enable/disable target publishing from the eyes' command topic."""
        try:
            v = msg.payload.decode(errors="ignore").strip().strip('"').lower()
        except Exception:
            return
        if v.startswith("{"):
            try:
                v = str(json.loads(msg.payload).get("state", "")).strip().lower()
            except Exception:
                return
        want = True if v in self.ON_WORDS else False if v in self.OFF_WORDS else None
        if want is None or want == self.enabled:
            return
        self.enabled = want
        print(f"[bridge] tracking {'enabled' if want else 'disabled'}", flush=True)
        if not want:
            self._release_target()     # release any held target as we go quiet
        if self._loop is not None:     # paho thread -> wake the HA connection loop
            self._loop.call_soon_threadsafe(self._sync_events)

    def _connect_mqtt(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ha-eye-bridge")
        if self.cfg.mqtt_user:
            self.client.username_pw_set(self.cfg.mqtt_user, self.cfg.mqtt_pass)
        if self.cfg.follow_command:
            self.client.message_callback_add(self.cfg.command_topic, self._on_command)
        self._setup_status()              # sets the last will, so before connect()

        def _on_connect(c, u, flags, rc, props):
            set_nodelay(c)                # send each small target message immediately
            if self.cfg.follow_command:
                c.subscribe(self.cfg.command_topic, qos=1)
                print(f"[bridge] following {self.cfg.command_topic} "
                      f"(currently {'enabled' if self.enabled else 'disabled'})", flush=True)
            if self.status:
                self.status.on_connect()
        self.client.on_connect = _on_connect
        self.client.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=30)
        self.client.loop_start()
        if self.status:
            self.status.start()

    # -- Home Assistant status (MQTT discovery; see ha_status.py) --
    def _setup_status(self):
        if not self.cfg.status_enabled:
            return
        c = self.cfg
        self.status = ha_status.StatusPublisher(
            self.client, c.status_node,
            ha_status.bridge_discovery(c.status_node, c.status_base, c.status_prefix),
            self.status_snapshot, base=c.status_base, prefix=c.status_prefix,
            refresh_s=c.status_refresh)
        print(f"[status] Home Assistant status on {c.status_base}/{c.status_node}/", flush=True)

    def status_snapshot(self):
        return {"tracking": bool(self.enabled and self.have), "enabled": bool(self.enabled),
                "ha_connected": bool(self.ha_connected), "host": self._host,
                "version": ha_status.VERSION}

    def _mark_seen(self, force=False):
        """Publish 'last target seen' on acquisition / loss, and at most every
        last_seen_every_s while a target is held. Retained and never cleared,
        so it survives the show going off and the bridge restarting."""
        if not self.status:
            return
        now = time.monotonic()
        if force or self._seen_pub is None or now - self._seen_pub >= self.cfg.last_seen_every:
            self.status.publish_last_seen()
            self._seen_pub = now

    def close(self):
        """Clean exit: mark the bridge offline now instead of via the last will."""
        if self.client is None:
            return
        if self.status:
            self.status.go_offline()
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def _publish_target(self, gx, gy):
        payload = json.dumps({"x": round(gx, 4), "y": round(gy, 4), "hold": self.cfg.hold})
        self.client.publish(self.cfg.topic, payload, qos=0)

    def _publish_clear(self):
        self.client.publish(self.cfg.topic, "clear", qos=0)

    def _release_target(self):
        """Drop any held target and tell the eyes, so the heartbeat stops
        re-sending it."""
        if self.have:
            self.have = False
            self._publish_clear()
            self._mark_seen(force=True)

    def evaluate(self, force=False):
        """Recompute from the cache and publish (rate-limited)."""
        if not self.enabled:
            return
        now = time.monotonic()
        target = self.pick_target()
        if target is None:
            if self.have:                       # just lost the target
                self.have = False
                self._publish_clear()
                self._mark_seen(force=True)     # the last moment someone was there
                print("[tgt] cleared", flush=True)
            return
        gx, gy = self.normalize(*target)
        if not self.have:                        # fresh acquisition: snap (no smoothing)
            self.sx, self.sy = gx, gy
            self.have = True
            self._mark_seen(force=True)
            print(f"[tgt] acquired  x={gx:+.2f} y={gy:+.2f}", flush=True)
        else:
            self._mark_seen()
            a = self.cfg.smooth
            self.sx += (gx - self.sx) * a
            self.sy += (gy - self.sy) * a
        if force or (now - self.last_pub) >= self.cfg.min_interval:
            self._publish_target(self.sx, self.sy)
            self.last_pub = now

    # -- Home Assistant WebSocket --
    async def _session(self):
        try:
            await self._ha_session()
        finally:
            self.ha_connected = False

    async def _ha_session(self):
        async with websockets.connect(self.cfg.url, max_size=8 * 1024 * 1024,
                                      ping_interval=20) as ws:
            # auth handshake
            hello = json.loads(await ws.recv())
            if hello.get("type") != "auth_required":
                raise RuntimeError(f"unexpected first message: {hello.get('type')}")
            await ws.send(json.dumps({"type": "auth", "access_token": self.cfg.token}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "auth_ok":
                raise RuntimeError(f"auth failed: {ack}")
            print("[ha] authenticated", flush=True)
            self.ha_connected = True

            # seed current states
            await ws.send(json.dumps({"id": 1, "type": "get_states"}))
            # subscribe to state changes
            await ws.send(json.dumps({"id": 2, "type": "subscribe_events",
                                      "event_type": "state_changed"}))

            async for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "result" and msg.get("id") == 1 and msg.get("success"):
                    for st in msg.get("result", []):
                        eid = st.get("entity_id")
                        if eid in self.cfg.entities:
                            self.cache[eid] = st.get("state")
                    print(f"[ha] seeded {len(self.cache)} entities", flush=True)
                    self.evaluate(force=True)
                elif mtype == "event":
                    data = msg.get("event", {}).get("data", {})
                    eid = data.get("entity_id")
                    if eid in self.cfg.entities:
                        ns = data.get("new_state") or {}
                        self.cache[eid] = ns.get("state")
                        self.evaluate()

    async def _heartbeat(self):
        # keep a stationary target alive so the eye doesn't time out on it
        while True:
            await asyncio.sleep(self.cfg.heartbeat)
            if self.enabled and self.have:
                self._publish_target(self.sx, self.sy)

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._on_evt, self._off_evt = asyncio.Event(), asyncio.Event()
        self._sync_events()
        self._connect_mqtt()
        asyncio.ensure_future(self._heartbeat())
        backoff = 1.0
        while True:
            if not self.enabled:
                # Show is off: no point holding a Home Assistant connection and
                # receiving every state change in the house. Wait for "on".
                print("[ha] show is off — not connected to Home Assistant until 'on'", flush=True)
                await self._on_evt.wait()
            started = time.monotonic()
            session = asyncio.ensure_future(self._session())
            went_off = asyncio.ensure_future(self._off_evt.wait())
            done, _ = await asyncio.wait({session, went_off},
                                         return_when=asyncio.FIRST_COMPLETED)
            if went_off in done:
                session.cancel()
                try:
                    await session
                except BaseException:
                    pass
                self.cache.clear()
                print("[ha] disconnected — show turned off", flush=True)
                backoff = 1.0
                continue
            went_off.cancel()
            e = session.exception()
            self.cache.clear()
            # Without HA we can't tell if the target is still there; don't let
            # the heartbeat keep the eyes staring at it for the whole outage.
            self._release_target()
            if time.monotonic() - started > 60:
                backoff = 1.0              # it was up a while: retry promptly
            print(f"[ha] disconnected: {e or 'closed by Home Assistant'} — "
                  f"retrying in {backoff:.0f}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0) if e else 1.0


def _sigterm(*_):
    raise KeyboardInterrupt      # systemctl stop -> same clean path as Ctrl-C


def main():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.ini")
    cfg = Config(sys.argv[1] if len(sys.argv) > 1 else cfg_path)
    if not cfg.token:
        print("No HA token. Set it in bridge.ini or the HA_TOKEN env var.", file=sys.stderr)
        sys.exit(1)
    signal.signal(signal.SIGTERM, _sigterm)
    bridge = Bridge(cfg)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
