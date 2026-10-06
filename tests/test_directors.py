"""Every director must emit exactly its renderer's schema keys, every frame,
with finite values — the follower rebuilds state from these keys by name."""
import math
import pytest
import directors, stage
from renderers import EyeRenderer, FigureRenderer, BallroomRenderer, SpiritsRenderer

CASES = [
    ("eye", directors.Director, EyeRenderer.schema),
    ("walk", lambda: stage.StageDirector(), stage.STAGE_NEUTRAL),
    ("drift", lambda: stage.DriftDirector(), stage.STAGE_NEUTRAL),
    ("ballroom", lambda: stage.BallroomDirector(), stage.BALLROOM_NEUTRAL),
    ("spirits", lambda: stage.SpiritDirector(), stage.SPIRIT_NEUTRAL),
]


@pytest.mark.parametrize("name,make,schema", CASES, ids=[c[0] for c in CASES])
def test_director_conforms_to_schema(name, make, schema):
    d = make()
    for _ in range(600):                      # 10 simulated seconds
        s = d.update(1 / 60)
        assert set(s) == set(schema), set(s) ^ set(schema)
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


def test_stride_keeps_feet_planted_at_any_speed():
    """Walk cycle advances by DISTANCE, so distance-per-cycle is constant."""
    for speed in (0.04, 0.085, 0.2):
        d = stage.StageDirector(speed=speed, stride=0.075)
        d.pos, d.facing, d.paused, d.next_pause_at = 0.0, 1.0, 0.0, None
        cycles, prev, n = 0.0, d.phase, 0
        while d.pos < 0.9 and n < 100000:
            d.update(1 / 60); n += 1
            dph = d.phase - prev
            if dph < -0.5: dph += 1.0
            cycles += dph; prev = d.phase
        assert abs(d.pos / cycles - 0.075) < 0.002


def test_ballroom_dancers_are_staggered():
    d = stage.BallroomDirector()
    lit_first = [None] * stage.BALLROOM_SLOTS
    t = 0.0
    for _ in range(60 * 20):
        s = d.update(1 / 60); t += 1 / 60
        for i in range(stage.BALLROOM_SLOTS):
            if lit_first[i] is None and s[f"lit{i}"] > 0.5:
                lit_first[i] = t
    assert all(v is not None for v in lit_first)
    assert lit_first == sorted(lit_first) and lit_first[-1] - lit_first[0] > 3.0


def test_command_and_target_parsers():
    p = directors._parse_command
    assert p(b"on") is True and p(b"OFF") is False and p(b'{"state":"ON"}') is True
    assert p(b"garbage") is None
    t = directors._parse_target
    assert t(b'{"x":0.5,"y":-0.2,"hold":3}')[0] == "set"
    assert t(b"clear")[0] == "clear"
