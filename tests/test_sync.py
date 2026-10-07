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
