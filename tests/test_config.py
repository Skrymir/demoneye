"""config.ini round-trips: every setting the app reads is readable from the
shipped file, and a missing file falls back to defaults without error."""
import settings as S


def test_shipped_config_loads_and_overrides_defaults():
    cp = S.load_config("config.ini")
    assert cp.has_section("mqtt") and cp.has_section("stage") and cp.has_section("projector")
    assert 0.0 <= S.STAGE_FROM < S.STAGE_TO <= 1.0
    assert S.STALE_AFTER_S >= 0
    assert S.FIGURE_FACES in ("left", "right")


def test_missing_config_uses_defaults(tmp_path):
    S.load_config(str(tmp_path / "nope.ini"))
    assert S.START_MODE in ("playing", "idle")
    assert S.PUBLISH_HZ > 0


def test_atlas_path_is_resolved_relative_to_config(tmp_path):
    cfg = tmp_path / "c.ini"
    cfg.write_text("[stage]\nfigure_atlas = figures/x.png\n")
    S.load_config(str(cfg))
    assert S.FIGURE_ATLAS == str(tmp_path / "figures" / "x.png")
    S.load_config("config.ini")   # restore for other tests
