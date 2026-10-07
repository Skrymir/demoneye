"""config.ini round-trips: every setting the app reads is readable from the
shipped file, and a missing file falls back to defaults without error."""
import settings as S


def test_shipped_config_loads_and_overrides_defaults():
    cp = S.load_config("config.ini")
    assert cp.has_section("mqtt") and cp.has_section("display") and cp.has_section("projector")
    assert not cp.has_section("stage")          # stage mode was removed
    assert S.STALE_AFTER_S >= 0
    assert S.OUTPUT_MODE


def test_missing_config_uses_defaults(tmp_path):
    S.load_config(str(tmp_path / "nope.ini"))
    assert S.START_MODE in ("playing", "idle")
    assert S.PUBLISH_HZ > 0


def test_leftover_stage_section_is_ignored(tmp_path):
    """A Pi's own config.ini may still have [stage]; it must still load."""
    cfg = tmp_path / "c.ini"
    cfg.write_text("[stage]\nstage_from = 0.0\nfigure_atlas = figures/x.png\n[display]\nscale = 1.2\n")
    S.load_config(str(cfg))
    assert S.EYE_SCALE == 1.2
    S.load_config("config.ini")   # restore for other tests
