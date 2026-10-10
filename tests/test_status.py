"""Home Assistant status over MQTT discovery (ha_status.py), its use in the
bridge, the [status] config section and check_config's pair check."""

import json
import types

import pytest

import ha_status as H


class FakeInfo:
    def wait_for_publish(self, timeout=None):
        return True


class FakeClient:
    def __init__(self):
        self.published = []      # (topic, payload, qos, retain)
        self.will = None
        self.callbacks = {}
        self.subs = []

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def message_callback_add(self, topic, cb):
        self.callbacks[topic] = cb

    def subscribe(self, topic, qos=0):
        self.subs.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return FakeInfo()

    def last(self, topic):
        return next((p for t, p, *_ in reversed(self.published) if t == topic), None)


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


# ---------------------------------------------------------------- helpers
@pytest.mark.parametrize("node,host,want", [
    ("auto", "pi-eye-left", "left"), ("auto", "pi-eye-right.home", "right"),
    ("auto", "garage-pi", "garage_pi"), ("Left", "whatever", "left"),
    ("porch eye", "x", "porch_eye"), ("", "pi-eye-left", "left"),
])
def test_resolve_node(node, host, want):
    assert H.resolve_node(node, host) == want


def test_show_state():
    assert H.show_state(False, True, False) == "idle"
    assert H.show_state(False, False, True) == "idle"          # off wins
    assert H.show_state(True, False, False) == "waiting_for_display"
    assert H.show_state(True, True, True) == "lost_brain"
    assert H.show_state(True, True, False) == "playing"
    assert set(H.SHOW_STATES) == {"playing", "idle", "waiting_for_display", "lost_brain"}


def test_projector_word():
    assert [H.projector_word(v) for v in (True, False, None)] == ["on", "off", "unknown"]


def test_cached_probe_rate_limits():
    calls, clk = [], Clock()
    p = H.CachedProbe(lambda: calls.append(1) or len(calls), 5, clock=clk)
    assert p() == 1 and p() == 1
    clk.t = 4.9; assert p() == 1
    clk.t = 5.0; assert p() == 2
    bad = H.CachedProbe(lambda: 1 / 0, 5, clock=clk)
    assert bad() is None                                         # never raises


# -------------------------------------------------------------- discovery
def test_eye_discovery_entities():
    d = H.eye_discovery("left", command_topic=None)
    ids = {cfg["default_entity_id"] for cfg in d.values()}
    assert ids == {"binary_sensor.eye_left_running", "binary_sensor.projector_left",
                   "sensor.eyes_left_show_state", "sensor.eyes_left_role"}
    for topic, cfg in d.items():
        comp = cfg["default_entity_id"].split(".")[0]
        assert topic == f"homeassistant/{comp}/{cfg['unique_id']}/config"
        assert cfg["device"]["identifiers"] == ["demoneye_left"]
        assert "object_id" not in cfg                         # deprecated in HA
        if cfg["name"] != "Running":                          # running IS the availability
            assert cfg["availability_topic"] == "house/status/left/availability"
    run = d["homeassistant/binary_sensor/demoneye_left_running/config"]
    assert "availability_topic" not in run                    # shows Off, not Unavailable
    assert run["state_topic"].endswith("/left/availability")


def test_only_brain_gets_show_switch():
    assert not any("/switch/" in t for t in H.eye_discovery("right"))
    d = H.eye_discovery("left", command_topic="house/show/command")
    sw = d["homeassistant/switch/demoneye_show/config"]
    assert sw["command_topic"] == sw["state_topic"] == "house/show/command"
    assert sw["retain"] is True and sw["qos"] == 1
    assert sw["default_entity_id"] == "switch.halloween_eyes_show"


def test_bridge_discovery_entities():
    d = H.bridge_discovery()
    ids = {cfg["default_entity_id"] for cfg in d.values()}
    assert ids == {"binary_sensor.eye_bridge_running", "binary_sensor.tracking_active",
                   "sensor.last_target_seen", "sensor.bridge_host",
                   "binary_sensor.eye_bridge_ha_connected"}
    seen = d["homeassistant/sensor/demoneye_bridge_last_target_seen/config"]
    assert seen["device_class"] == "timestamp"
    assert seen["state_topic"] == "house/status/bridge/last_seen"
    assert "availability_topic" not in seen                   # survives the bridge going down


def test_unique_ids_are_unique_across_nodes():
    every = {**H.eye_discovery("left", command_topic="c"), **H.eye_discovery("right"),
             **H.bridge_discovery()}
    uids = [c["unique_id"] for c in every.values()]
    assert len(uids) == len(set(uids)) == 4 + 1 + 4 + 5


# HA renders these with Jinja; check the templates do what we mean. (Skipped
# where jinja2 isn't installed; everything else in this file still runs.)
def _render(tmpl, payload):
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment()
    env.filters["tojson"] = lambda v: json.dumps(v)
    try:
        vj = json.loads(payload)
    except ValueError:
        vj = None
    return env.from_string(tmpl).render(value=payload, value_json=vj).strip()


def test_projector_template():
    t = H.eye_discovery("left")["homeassistant/binary_sensor/demoneye_left_projector/config"]
    tm = t["value_template"]
    assert _render(tm, '{"projector": "on"}') == "ON"
    assert _render(tm, '{"projector": "off"}') == "OFF"
    assert _render(tm, '{"projector": "unknown"}') == "None"  # HA: unknown state


@pytest.mark.parametrize("payload,want", [
    ("on", "on"), ("OFF", "off"), ("play", "on"), ("stop", "off"),
    ('{"state": "ON"}', "on"), ('{"state":"off"}', "off"),
])
def test_switch_template_understands_every_command_form(payload, want):
    sw = H.eye_discovery("left", command_topic="c")["homeassistant/switch/demoneye_show/config"]
    assert _render(sw["value_template"], payload) == want


# -------------------------------------------------------------- publisher
def make_pub(snap, refresh=30):
    c, clk = FakeClient(), Clock()
    p = H.StatusPublisher(c, "left", H.eye_discovery("left"), lambda: dict(snap),
                          refresh_s=refresh, clock=clk)
    return c, clk, p


def test_last_will_is_retained_offline():
    c, _, _ = make_pub({"show": "idle"})
    assert c.will == ("house/status/left/availability", "offline", 1, True)


def test_on_connect_publishes_discovery_online_and_state_retained():
    c, _, p = make_pub({"show": "idle"})
    p.on_connect()
    topics = [t for t, *_ in c.published]
    assert sum(t.startswith("homeassistant/") for t in topics) == 4
    assert c.last("house/status/left/availability") == "online"
    assert json.loads(c.last("house/status/left/state")) == {"show": "idle"}
    assert all(retain for *_, retain in c.published)
    assert "homeassistant/status" in c.subs


def test_publishes_on_change_and_refresh_only():
    snap = {"show": "idle"}
    c, clk, p = make_pub(snap, refresh=30)
    assert p.tick() is True                     # first one always goes
    clk.t = 10; assert p.tick() is False        # nothing changed
    snap["show"] = "playing"
    clk.t = 11; assert p.tick() is True         # change goes straight away
    clk.t = 40; assert p.tick() is False
    clk.t = 41; assert p.tick() is True         # refresh
    states = [p_ for t, p_, *_ in c.published if t.endswith("/state")]
    assert len(states) == 3


def test_ha_birth_message_republishes_discovery():
    c, _, p = make_pub({"show": "idle"})
    c.callbacks["homeassistant/status"](None, None, types.SimpleNamespace(payload=b"online"))
    assert sum(t.startswith("homeassistant/") for t, *_ in c.published) == 4


def test_go_offline():
    c, _, p = make_pub({"show": "idle"})
    p.go_offline()
    assert c.published[-1] == ("house/status/left/availability", "offline", 1, True)


def test_snapshot_error_never_raises():
    c = FakeClient()
    p = H.StatusPublisher(c, "left", {}, lambda: 1 / 0)
    assert p.tick() is False and c.published == []


# ------------------------------------------------------------------ config
def test_settings_status_defaults_and_override(tmp_path):
    import settings as S
    S.load_config(str(tmp_path / "missing.ini"))
    assert S.STATUS_ENABLED is True and S.STATUS_NODE == "auto"
    assert S.STATUS_BASE_TOPIC == "house/status"
    cfg = tmp_path / "c.ini"
    cfg.write_text("[status]\nenabled = false\nnode = porch\nbase_topic = a/b/\nrefresh_s = 45\n")
    S.load_config(str(cfg))
    assert (S.STATUS_ENABLED, S.STATUS_NODE, S.STATUS_BASE_TOPIC, S.STATUS_REFRESH_S) == \
        (False, "porch", "a/b", 45.0)
    S.load_config("config.ini")                  # restore for other tests
    assert S.STATUS_ENABLED is True


def test_check_config_knows_status_keys_and_catches_duplicate_nodes():
    import configparser
    import check_config as CC
    assert set(CC.SCHEMA["status"]) == set(H.STATUS_DEFAULTS)
    assert "last_seen_every_s" in CC.BRIDGE_SCHEMA["status"]

    def cp(text):
        c = configparser.ConfigParser(); c.read_string(text); return c
    auto = cp("[mqtt]\nhost = x\n")
    assert CC.status_node(auto, "pi-eye-left.home") == H.resolve_node("auto", "pi-eye-left.home")

    rep = CC.Report("pair")
    CC.check_status_pair(rep, [("pi-eye-left.home", auto, {}), ("pi-eye-right.home", auto, {})])
    assert rep.errors == 0
    rep = CC.Report("pair")
    same = cp("[status]\nnode = eye\n")
    CC.check_status_pair(rep, [("a", same, {}), ("b", same, {})])
    assert rep.errors == 1


# ------------------------------------------------------------------ bridge
def _bridge():
    pytest.importorskip("websockets")
    pytest.importorskip("paho.mqtt")
    import ha_bridge
    cfg = ha_bridge.Config(None)
    cfg.start_enabled = True
    b = ha_bridge.Bridge(cfg)
    b.client = FakeClient()
    b._setup_status()
    return b


def test_bridge_status_config_defaults():
    pytest.importorskip("websockets")
    import ha_bridge
    cfg = ha_bridge.Config(None)
    assert cfg.status_enabled and cfg.status_node == "bridge"
    assert cfg.status_refresh == 60 and cfg.last_seen_every == 10


def test_bridge_snapshot_and_last_seen(monkeypatch):
    b = _bridge()
    seen_topic = "house/status/bridge/last_seen"
    assert b.status_snapshot()["tracking"] is False
    t0 = b.cfg.targets[0]
    b.cache = {t0["x"]: "120", t0["y"]: "1500"}

    clock = [100.0]
    monkeypatch.setattr("ha_bridge.time.monotonic", lambda: clock[0])
    b.evaluate(force=True)                                   # acquisition
    assert b.status_snapshot()["tracking"] is True
    n = lambda: sum(t == seen_topic for t, *_ in b.client.published)
    assert n() == 1
    clock[0] = 105; b.evaluate(); assert n() == 1            # throttled while held
    clock[0] = 111; b.evaluate(); assert n() == 2            # every last_seen_every_s
    b.cache = {}
    clock[0] = 112; b.evaluate(); assert n() == 3            # loss = last moment seen
    assert b.status_snapshot()["tracking"] is False
    assert b.client.last(seen_topic).endswith("+00:00")


def test_bridge_close_publishes_offline():
    b = _bridge()
    b.client.loop_stop = lambda: None
    b.client.disconnect = lambda: None
    b.close()
    assert ("house/status/bridge/availability", "offline", 1, True) in b.client.published


class FlakyClient(FakeClient):
    """Broker hiccups: the first few publishes raise."""
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def publish(self, topic, payload, qos=0, retain=False):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("broker went away")
        return super().publish(topic, payload, qos, retain)


def test_status_thread_survives_publish_errors():
    """A publish error must not kill the status thread: HA would keep showing
    frozen state that still says 'online'."""
    import time
    c = FlakyClient(failures=3)
    p = H.StatusPublisher(c, "left", {}, lambda: {"show": "playing"}, tick_s=0.01)
    p.start()
    try:
        deadline = time.monotonic() + 3
        while not c.published and time.monotonic() < deadline:
            time.sleep(0.01)
        assert p._thread.is_alive()
        assert c.last(p.topics["state"]) is not None, "never published after the errors"
    finally:
        p.go_offline()


def test_failed_publish_is_retried_next_tick():
    """If publishing a snapshot fails, the next tick must retry it rather than
    think it already went out (and wait for the slow refresh)."""
    c = FlakyClient(failures=1)
    p = H.StatusPublisher(c, "left", {}, lambda: {"show": "playing"}, refresh_s=1e9)
    with pytest.raises(RuntimeError):
        p.tick()
    assert p.tick() is True and c.last(p.topics["state"]) is not None
