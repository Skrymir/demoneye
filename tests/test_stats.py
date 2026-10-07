"""--stats: fps, late frames (missed refreshes) and p95 frame time."""

import stats


def test_late_frames_and_p95(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(stats.time, "monotonic", lambda: t[0])
    s = stats.Stats(interval=1.0, refresh_hz=60)
    s.gpu_src = None
    monkeypatch.setattr(s, "cpu_percent", lambda: None)
    monkeypatch.setattr(s, "temp_c", lambda: None)
    line = None
    # 50 smooth frames, then 5 that each miss a refresh (33 ms), ~1 s total
    for ms in [16.7] * 50 + [33.4] * 5:
        t[0] += ms / 1000
        line = s.frame() or line
    t[0] += 0.2
    line = s.frame() or line
    assert line is not None
    assert "late" in line and "p95" in line
    late = int(line.split("late")[1].split("/s")[0])
    assert 5 <= late <= 6            # the 33 ms frames (+ the final long gap)


def test_late_threshold_follows_refresh():
    assert round(stats.Stats(refresh_hz=50)._late_ms) == 30
    assert round(stats.Stats()._late_ms) == 25        # unknown -> assume 60 Hz
