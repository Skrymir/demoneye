"""ha_bridge on/off: publishes nothing and holds no Home Assistant connection
while the show is off; 'on' connects, 'off' clears the target and disconnects."""

import asyncio
import threading
import types

import pytest

pytest.importorskip("websockets")
pytest.importorskip("paho.mqtt")
import ha_bridge  # noqa: E402


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload))


def make_bridge(start_enabled=False):
    cfg = ha_bridge.Config(None)
    cfg.start_enabled = start_enabled
    b = ha_bridge.Bridge(cfg)
    b.client = FakeClient()
    b._connect_mqtt = lambda: None          # no broker in tests
    return b


def cmd(b, payload):
    """Deliver a command the way paho does: from another thread."""
    msg = types.SimpleNamespace(payload=payload.encode())
    t = threading.Thread(target=b._on_command, args=(None, None, msg))
    t.start(); t.join()


def test_off_publishes_nothing():
    b = make_bridge(start_enabled=False)
    b.cache = {b.cfg.targets[0]["x"]: "120", b.cfg.targets[0]["y"]: "1500"}
    b.evaluate(force=True)
    assert b.client.published == []


def test_on_off_connects_and_disconnects_ha():
    b = make_bridge(start_enabled=False)
    sessions = {"opened": 0, "open": 0}

    async def fake_session():
        sessions["opened"] += 1; sessions["open"] += 1
        try:
            await asyncio.sleep(3600)
        finally:
            sessions["open"] -= 1
    b._session = fake_session

    async def heartbeat():
        await asyncio.sleep(3600)
    b._heartbeat = heartbeat

    async def scenario():
        task = asyncio.ensure_future(b.run())
        await asyncio.sleep(0.05)
        assert sessions["opened"] == 0, "connected to HA while the show is off"

        cmd(b, "on")
        await asyncio.sleep(0.05)
        assert sessions["open"] == 1

        b.have = True                       # pretend someone is being tracked
        cmd(b, "off")
        await asyncio.sleep(0.05)
        assert sessions["open"] == 0, "still connected to HA after 'off'"
        assert b.client.published[-1] == (b.cfg.topic, "clear")

        cmd(b, '{"state": "ON"}')          # JSON form works too
        await asyncio.sleep(0.05)
        assert sessions["opened"] == 2 and sessions["open"] == 1
        task.cancel()

    asyncio.run(scenario())


def test_follow_command_false_always_on():
    cfg = ha_bridge.Config(None)
    cfg.follow_command = False
    assert ha_bridge.Bridge(cfg).enabled


def test_ha_dropping_releases_the_target():
    """HA goes away mid-show: clear the target instead of letting the heartbeat
    keep the eyes staring at the last point for the whole outage."""
    b = make_bridge(start_enabled=True)
    b.cfg.heartbeat = 0.02

    async def dropping_session():
        await asyncio.sleep(0.02)
        raise ConnectionError("HA restarted")
    b._session = dropping_session

    async def scenario():
        b.have, b.sx, b.sy = True, 0.4, 0.0  # someone is being tracked
        task = asyncio.ensure_future(b.run())
        await asyncio.sleep(0.2)              # drop, then back off; heartbeats tick
        task.cancel()

    asyncio.run(scenario())
    assert not b.have
    assert (b.cfg.topic, "clear") in b.client.published
    after = b.client.published[b.client.published.index((b.cfg.topic, "clear")) + 1:]
    assert not any(p != "clear" for _, p in after), "heartbeat kept re-sending the target"


def test_mqtt_credentials_from_env(monkeypatch):
    """/etc/ha-bridge.env can hold the broker login, so bridge.ini stays blank."""
    monkeypatch.setenv("MQTT_USER", "eyes")
    monkeypatch.setenv("MQTT_PASS", "s3cret")
    cfg = ha_bridge.Config(None)
    assert (cfg.mqtt_user, cfg.mqtt_pass) == ("eyes", "s3cret")


def test_off_cannot_race_a_target_out_after_the_clear():
    """'off' (paho thread) arriving while evaluate() (asyncio thread) is midway
    must end with the eyes released: the last message is 'clear'."""
    import time
    b = make_bridge(start_enabled=True)
    inside, release = threading.Event(), threading.Event()

    def slow_pick():
        inside.set()
        release.wait(2)
        return (100.0, 1500.0)
    b.pick_target = slow_pick
    ev = threading.Thread(target=b.evaluate, kwargs={"force": True})
    ev.start()
    assert inside.wait(2)
    b.have = True                              # a target is already held
    off = threading.Thread(target=b._on_command,
                           args=(None, None, types.SimpleNamespace(payload=b"off")))
    off.start()
    time.sleep(0.1)                            # 'off' lands mid-evaluate
    release.set()
    ev.join(2); off.join(2)
    assert b.client.published[-1] == (b.cfg.topic, "clear"), b.client.published
    assert not b.have


class _RecordingMqtt:
    """Stands in for paho's Client: records calls, does nothing."""
    def __init__(self, *a, **kw):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append(name)
            return types.SimpleNamespace(wait_for_publish=lambda timeout=None: True)
        return call


def _reason(name):
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode
    return ReasonCode(PacketTypes.CONNACK, name)


def test_bridge_connects_in_background_and_ignores_a_refused_connect(monkeypatch):
    monkeypatch.setattr(ha_bridge.mqtt, "Client", _RecordingMqtt)
    b = ha_bridge.Bridge(ha_bridge.Config(None))
    b.status = None
    monkeypatch.setattr(b, "_setup_status", lambda: None)
    b._connect_mqtt()
    c = b.client
    assert "connect_async" in c.calls and "connect" not in c.calls, "a blocking connect"

    c.calls.clear()
    c.on_connect(c, None, None, _reason("Not authorized"), None)
    assert "subscribe" not in c.calls, "subscribed on a refused connection"
    c.on_connect(c, None, None, _reason("Success"), None)
    assert "subscribe" in c.calls
