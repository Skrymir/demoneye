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
