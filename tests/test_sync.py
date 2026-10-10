"""Eye sync: the eyes' and the bridge's MQTT sockets have Nagle off, so small
50 Hz messages aren't held back and released in 40-200 ms clumps."""

import socket

import pytest


class _Client:
    def __init__(self, sock):
        self._sock = sock

    def socket(self):
        return self._sock


def _check(set_nodelay):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert set_nodelay(_Client(sock))
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    finally:
        sock.close()
    assert set_nodelay(_Client(None)) is False      # not connected yet: no crash


def test_eye_sets_nodelay():
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    import demoneye
    _check(demoneye.set_nodelay)


def test_bridge_sets_nodelay():
    pytest.importorskip("websockets")
    pytest.importorskip("paho.mqtt")
    import ha_bridge
    _check(ha_bridge.set_nodelay)


def test_publish_every_frame_at_60fps():
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    from demoneye import publish_every
    # the old 50 Hz timer fired every other 60 fps frame (30 msg/s)
    assert publish_every(60, 50) == 1
    assert publish_every(60, 60) == 1
    assert publish_every(60, 30) == 2
    assert publish_every(60, 0) == 60          # never divide by zero


def test_output_mode_parsing():
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    from demoneye import output_size
    assert output_size("1280x720") == (1280, 720)
    assert output_size(" 1920 X 1080 ") == (1920, 1080)
    for native in ("native", "auto", "", "720p", "0x0", None):
        assert output_size(native) == (0, 0)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_eye_keeps_retrying_until_the_broker_appears(monkeypatch):
    """Power cut: the Pi boots before the broker. The first connect fails, but
    the eye must keep trying (not fall back to solo for the night) and reach
    the broker once it's up."""
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    pytest.importorskip("paho.mqtt")
    import threading
    import time
    import demoneye
    port = _free_port()                       # nothing listening yet
    monkeypatch.setattr(demoneye.S, "MQTT_HOST", "127.0.0.1")
    monkeypatch.setattr(demoneye.S, "MQTT_PORT", port)
    monkeypatch.setattr(demoneye.S, "STATUS_ENABLED", False)
    failed = threading.Event()
    app = object.__new__(demoneye.EyeApp)
    app.role, app.status = "follower", None
    app._on_connect_fail = lambda *a: failed.set()
    app._setup_mqtt()
    try:
        assert app.client is not None, "gave up and went solo"
        assert failed.wait(5), "never attempted to connect"
        broker = socket.socket()
        broker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        broker.bind(("127.0.0.1", port))
        broker.listen(1)
        broker.settimeout(10)
        try:
            conn, _ = broker.accept()         # the retry reaches the new broker
            conn.close()
        finally:
            broker.close()
    finally:
        app.client.loop_stop()


def test_follower_goes_dark_despite_clock_steps(monkeypatch):
    """The watchdog runs on the monotonic clock: NTP stepping the wall clock
    back after boot must not stop a silent follower going dark."""
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    import time
    import types
    import demoneye
    monkeypatch.setattr(demoneye.S, "STALE_AFTER_S", 0.05)
    app = object.__new__(demoneye.EyeApp)
    app.solo, app.client, app.stale = False, object(), False
    app.schema = {"x": 0.0}
    app._on_message(None, None, types.SimpleNamespace(topic=demoneye.S.TOPIC, payload=b'{"x": 1}'))
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 3600)   # clock stepped back
    time.sleep(0.1)                           # ...and the brain has gone quiet
    app._watchdog()
    assert app.stale


def test_ctrl_c_takes_the_clean_exit_path():
    """Ctrl-C stops the loop (so run() reaches _exit_now) instead of raising
    out of it; a second Ctrl-C still force-quits."""
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    import os
    import signal
    import demoneye
    saved = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    app = object.__new__(demoneye.EyeApp)
    app.running = True
    try:
        app._install_signal_handlers()
        os.kill(os.getpid(), signal.SIGINT)   # handler runs here; must not raise
        assert app.running is False
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGINT)
    finally:
        signal.signal(signal.SIGINT, saved[0])
        signal.signal(signal.SIGTERM, saved[1])


def test_follower_ignores_non_finite_state():
    """One NaN in the state stream used to stick in the smoothed state until
    restart. It's dropped instead, and good state still flows afterwards."""
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    import math
    import types
    import demoneye
    app = object.__new__(demoneye.EyeApp)
    app.schema = {"gx": 0.0, "gy": 0.0}
    app.lerp = {}
    app.net, app.disp = dict(app.schema), dict(app.schema)
    app.last_state_at = 0.0

    def send(payload):
        app._on_message(None, None, types.SimpleNamespace(topic=demoneye.S.TOPIC, payload=payload))
        app._smooth(1 / 60)

    send(b'{"gx": NaN, "gy": 0.5}')
    send(b'{"gx": 0.2, "gy": Infinity}')
    assert app.last_state_at == 0.0, "bad state counted as hearing from the brain"
    send(b'{"gx": 0.3, "gy": 0.1}')
    assert app.net == {"gx": 0.3, "gy": 0.1}
    assert app.last_state_at > 0
    assert all(math.isfinite(v) for v in app.disp.values())


def test_eye_ignores_a_refused_connect():
    """Bad credentials: CONNACK says no. Don't subscribe or claim 'online'."""
    pytest.importorskip("pygame")
    pytest.importorskip("OpenGL")
    pytest.importorskip("paho.mqtt")
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode
    import demoneye

    class Client:
        def __init__(self):
            self.subs = []

        def subscribe(self, topic, qos=0):
            self.subs.append(topic)

        def socket(self):
            return None
    app = object.__new__(demoneye.EyeApp)
    app.role, app.status = "brain", None
    c = Client()
    app._on_connect(c, None, None, ReasonCode(PacketTypes.CONNACK, "Not authorized"), None)
    assert c.subs == []
    app._on_connect(c, None, None, ReasonCode(PacketTypes.CONNACK, "Success"), None)
    assert demoneye.S.COMMAND_TOPIC in c.subs
