"""The Christmas scene: the director streams the renderer's schema with
finite values, the sleigh crosses the whole two-projector world, a passer-by
launches it (with a cooldown) and makes a gust that follows them."""
import math
import random

import pytest

pytest.importorskip("OpenGL")
import christmas as C  # noqa: E402
from renderers import EYE_FIGURES, make_renderer  # noqa: E402

DT = 1 / 60


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def director():
    clk = Clock()
    return C.ChristmasDirector(rng=random.Random(1), clock=clk), clk


def run(d, clk, seconds, target=None):
    for _ in range(int(seconds / DT)):
        if target is not None:
            d.set_target(target(), 0.0, hold=1.0)
        clk.t += DT
        s = d.update(DT)
    return s


def test_director_conforms_to_schema():
    d, clk = director()
    for _ in range(int(40 / DT)):
        clk.t += DT
        s = d.update(DT)
        assert set(s) == set(C.NEUTRAL)
        assert all(math.isfinite(v) for v in s.values())


def test_first_flight_crosses_the_whole_world():
    d, clk = director()
    run(d, clk, C.FIRST_FLIGHT + 0.1)
    assert d.flying and d.s["sleigh_on"] == 1.0
    start = d.s["sleigh_x"]
    assert abs(start) > C.SPAN                     # starts off-screen
    xs = []
    while d.flying:
        clk.t += DT
        xs.append(d.update(DT)["sleigh_x"])
    assert d.s["sleigh_on"] == 0.0
    assert min(xs) < -C.SPAN and max(xs) > C.SPAN  # over BOTH projectors
    flight = len(xs) * DT
    assert 5 < flight < 15, flight                 # a few seconds, not a blink


def test_passer_by_launches_the_sleigh_and_gets_a_gust():
    d, clk = director()
    d.next_idle = 1e9                              # no idle flights in this test
    s = run(d, clk, 2.0, target=lambda: 0.5)
    assert d.flying, "someone walked up and nothing happened"
    assert s["gust"] > 0.9
    assert s["gust_x"] == pytest.approx(0.5 * C.SPAN, abs=0.05)


def test_sleigh_has_a_cooldown():
    d, clk = director()
    d.next_idle = 1e9
    run(d, clk, 1.0, target=lambda: 0.5)
    while d.flying:
        clk.t += DT
        d.update(DT)
    run(d, clk, 2.0)                               # they leave...
    run(d, clk, 1.0, target=lambda: -0.5)          # ...someone else arrives at once
    assert not d.flying, "relaunched inside the cooldown"
    run(d, clk, C.SLEIGH_COOLDOWN)
    run(d, clk, 1.0, target=lambda: 0.3)           # after the cooldown it flies
    assert d.flying


def test_walking_pushes_the_wind_their_way():
    d, clk = director()
    d.next_idle = 1e9
    x = [-0.8]

    def walk_right():
        x[0] = min(0.8, x[0] + 0.25 * DT)          # a slow walk left -> right
        return x[0]
    s = run(d, clk, 5.0, target=walk_right)
    assert s["wind"] > 0.3


def test_gust_dies_down_after_they_leave():
    d, clk = director()
    run(d, clk, 2.0, target=lambda: 0.0)
    s = run(d, clk, 10.0)
    assert s["gust"] < 0.05


def test_views_split_the_world_between_the_projectors():
    a = C.ASPECT
    lc, lh = C.view_for("left", a)
    rc, rh = C.view_for("right", a)
    bc, bh = C.view_for("both", a)
    assert lc + lh <= rc - rh                      # left is left of right, no overlap
    assert bc - bh <= lc - lh and bc + bh >= rc + rh   # "both" shows all of it


@pytest.mark.parametrize("env, want", [("left", "left"), ("RIGHT", "right"),
                                       ("both", "both")])
def test_side_override(monkeypatch, env, want):
    monkeypatch.setenv("CHRISTMAS_SIDE", env)
    assert C.resolve_side() == want


def test_unknown_host_shows_both(monkeypatch):
    monkeypatch.delenv("CHRISTMAS_SIDE", raising=False)
    monkeypatch.setattr("ha_status.hostname", lambda: "joshs-macbook")
    monkeypatch.setattr(C.S, "STATUS_NODE", "auto")
    assert C.resolve_side() == "both"


def test_it_is_a_selectable_scene():
    assert "christmas" in EYE_FIGURES
    r = make_renderer("christmas")
    assert r.schema is C.NEUTRAL and r.frag is C.CHRISTMAS_FRAG
    assert isinstance(r.make_director(), C.ChristmasDirector)
