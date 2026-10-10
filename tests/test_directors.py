"""The eye director must emit exactly the renderer's schema keys, every frame,
with finite values — the follower rebuilds state from these keys by name."""
import math
import directors
from renderers import EyeRenderer


def test_director_conforms_to_schema():
    d = directors.Director()
    for _ in range(600):                      # 10 simulated seconds
        s = d.update(1 / 60)
        assert set(s) == set(EyeRenderer.schema), set(s) ^ set(EyeRenderer.schema)
        assert all(math.isfinite(v) for v in s.values())


def test_eye_tracking_acquires_and_releases():
    d = directors.Director()
    for _ in range(30): d.update(1 / 60)
    d.set_target(0.6, 0.0, hold=0.5)
    for _ in range(60): d.update(1 / 60)
    assert d.mood in ("surprise", "anger", "neutral", "glare")
    assert d.tracking
    import time; time.sleep(0.6)              # let the hold expire
    for _ in range(30): d.update(1 / 60)
    assert not d.tracking and d.mood == "neutral"


def test_command_and_target_parsers():
    p = directors._parse_command
    assert p(b"on") is True and p(b"OFF") is False and p(b'{"state":"ON"}') is True
    assert p(b"garbage") is None
    t = directors._parse_target
    assert t(b'{"x":0.5,"y":-0.2,"hold":3}')[0] == "set"
    assert t(b"clear")[0] == "clear"


class _ClearedMidFrame(directors.Director):
    """A 'clear' from the MQTT thread lands partway through update(): the
    first two reads of the target see it, every later read sees None."""
    @property
    def track(self):
        self.__dict__["_reads"] += 1
        return self.__dict__["_t"] if self.__dict__["_reads"] <= 2 else None

    @track.setter
    def track(self, v):
        self.__dict__["_t"], self.__dict__["_reads"] = v, 0


def test_target_cleared_mid_frame_does_not_crash():
    d = _ClearedMidFrame()
    for _ in range(5):
        d.set_target(0.5, 0.0, hold=2.0)
        d.update(1 / 60)                      # used to raise TypeError on unpack


def test_target_set_clear_from_another_thread():
    import threading, time
    d = directors.Director()
    stop = threading.Event()

    def mqtt_thread():
        while not stop.is_set():
            d.set_target(0.3, 0.1, hold=1.0)
            d.clear_target()
    t = threading.Thread(target=mqtt_thread)
    t.start()
    try:
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            d.update(1 / 60)
    finally:
        stop.set(); t.join()


def test_target_hold_survives_clock_step(monkeypatch):
    """NTP stepping the wall clock forward must not expire a held target."""
    import time
    d = directors.Director()
    d.set_target(0.6, 0.0, hold=5.0)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    for _ in range(10): d.update(1 / 60)
    assert d.tracking
