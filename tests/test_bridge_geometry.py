"""ha_bridge sensor-placement math (geometry = true)."""

import math

import pytest

pytest.importorskip("websockets")
pytest.importorskip("paho.mqtt")
import ha_bridge  # noqa: E402


def bridge(**cal):
    cfg = ha_bridge.Config(None)
    cfg.geometry = True
    cfg.unit_mm = 1.0
    cfg.sensor_x = cfg.sensor_y = 0.0
    cfg.yaw = 0.0
    cfg.flip_x = False
    cfg.gaze_max = 60.0
    cfg.invert_x = False
    cfg.y_from_distance = False
    for k, v in cal.items():
        setattr(cfg, k, v)
    return ha_bridge.Bridge(cfg)


def test_defaults_keep_legacy_mapping():
    cfg = ha_bridge.Config(None)
    assert cfg.geometry is False
    b = ha_bridge.Bridge(cfg)
    assert b.normalize(125, 3000) == (0.5, 0.0)


def test_sensor_at_eyes_unrotated_is_pure_bearing():
    b = bridge()
    assert b.normalize(0, 3000)[0] == pytest.approx(0.0)
    # 45 deg to the house-right -> pupil to screen-left, 45/60 of a full look
    assert b.normalize(3000, 3000)[0] == pytest.approx(-0.75)


def test_yaw_left_turns_straight_ahead_left():
    b = bridge(yaw=math.radians(15))
    wx, wy = b.to_world(0, 4000)
    assert wx == pytest.approx(-4000 * math.sin(math.radians(15)))
    assert wy == pytest.approx(4000 * math.cos(math.radians(15)))
    # sensor at the eyes: bearing is exactly the yaw, 15 deg to the left
    assert b.normalize(0, 4000)[0] == pytest.approx(15 / 60)


def test_driveway_example():
    # sensor 4 m right of the eyes, 6 m out, turned 15 deg left;
    # person 4 m straight ahead of it
    b = bridge(sensor_x=4000.0, sensor_y=6000.0, yaw=math.radians(15))
    wx, wy = b.to_world(0, 4000)
    assert wx == pytest.approx(4000 - 4000 * math.sin(math.radians(15)))
    assert wy == pytest.approx(6000 + 4000 * math.cos(math.radians(15)))
    bearing = math.degrees(math.atan2(wx, wy))
    assert b.normalize(0, 4000)[0] == pytest.approx(-bearing / 60)


def test_units_flip_invert_and_clamp():
    b = bridge(unit_mm=10.0, sensor_x=1000.0)
    assert b.to_world(100, 200) == pytest.approx((2000.0, 2000.0))
    b.cfg.flip_x = True
    assert b.to_world(100, 200) == pytest.approx((0.0, 2000.0))
    b.cfg.invert_x = True
    b.cfg.flip_x = False
    assert b.normalize(100, 200)[0] == pytest.approx(45 / 60)
    assert b.normalize(10000, 1)[0] == 1.0      # way off to the side: clamped


def test_distance_for_look_down_is_from_the_eyes():
    b = bridge(sensor_y=5000.0, y_from_distance=True, y_near=2000.0, y_far=8000.0)
    # sensor reads 0 m but the target is 5 m from the eyes: halfway-ish
    assert b.normalize(0, 1)[1] == pytest.approx(-(8000 - 5001) / 6000)
