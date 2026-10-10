"""Projector alignment test pattern (test_pattern.py): draws headless and puts
the eye outline where config.ini puts the eye."""
import pygame
import pytest

import settings as S
import test_pattern as tp


@pytest.fixture(autouse=True)
def _pygame():
    pygame.font.init()
    yield
    pygame.font.quit()


def _lit(surf, x, y):
    return surf.get_at((round(x), round(y)))[:3] != (0, 0, 0)


def test_eye_geometry_matches_renderer(monkeypatch):
    monkeypatch.setattr(S, "EYE_CENTER_X", 0.25)
    monkeypatch.setattr(S, "EYE_CENTER_Y", 0.6)
    monkeypatch.setattr(S, "EYE_SCALE", 1.5)
    cx, cy, r = tp.eye_geometry(1280, 720)
    assert (cx, cy) == (320, 432)
    assert r == pytest.approx(720 * 0.42 * 1.5)      # renderers.py uEyeR


def test_walker_lid_outline_shape():
    pts = tp.walker_lid_points(640, 360, 100)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert min(xs) == pytest.approx(640 - 115) and max(xs) == pytest.approx(640 + 115)
    assert min(ys) == pytest.approx(360 - 50)       # upper lid, y down on screen
    assert max(ys) == pytest.approx(360 + 46)


def test_draw_frame_and_eye():
    surf = pygame.Surface((1280, 720))
    tp.draw(surf, "LEFT", (255, 255, 255), eye=(640, 360, 302.4))
    assert _lit(surf, 1, 360)                  # border
    assert _lit(surf, 640 + 128, 360 - 20)     # 10% tick
    assert _lit(surf, 640 + 302, 360)          # eye circle
    assert not _lit(surf, 640 + 200, 360 - 200)  # empty space stays dark


def test_draw_without_eye_or_grid():
    surf = pygame.Surface((1280, 720))
    tp.draw(surf, "X", (255, 0, 0), show_grid=False, eye=None)
    assert not _lit(surf, 640 + 302, 300)


def test_png_uses_config(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[display]\ncenter_x = 0.30\noutput_mode = 1280x720\n")
    out = tmp_path / "p.png"
    try:
        assert tp.main(["--config", str(cfg), "--png", str(out), "--label", "T"]) == 0
    finally:
        S.load_config(None)                      # back to defaults for other tests
    img = pygame.image.load(str(out))
    assert img.get_size() == (1280, 720)
    assert _lit(img, 1280 * 0.30 + 720 * 0.42, 360)   # eye circle moved with center_x
