"""Closed-loop projector power control against a simulated toggle projector:
POWER toggles, shutdown needs a confirming 2nd press, HDMI appears after boot."""
import threading
import time

import pytest

import ir_control as IR


class Projector:
    def __init__(self, path, on=False, boot=0.3):
        self.path, self.on, self.boot = path, on, boot
        self.prompt_until, self.miss_next, self.presses = 0.0, 0, 0
        self._write(on)

    def _write(self, v):
        self.path.write_text("connected\n" if v else "disconnected\n")

    def power(self):
        self.presses += 1
        if self.miss_next:
            self.miss_next -= 1
            return
        now = time.time()
        if not self.on:
            if now < self.prompt_until:
                return
            self.on = True
            threading.Timer(self.boot, lambda: self._write(self.on)).start()
        elif now < self.prompt_until:
            self.on, self.prompt_until = False, 0.0
            self._write(False)
        else:
            self.prompt_until = now + 3.0


@pytest.fixture
def proj(tmp_path, monkeypatch):
    def make(on=False, miss=0, forced=False):
        p = Projector(tmp_path / "status", on=on)
        p.miss_next = miss
        monkeypatch.setattr(IR, "send_key", lambda key, **kw: (p.power(), True)[1])
        monkeypatch.setattr(IR, "_forced_mode", lambda: forced)
        monkeypatch.setattr(IR, "_warned_forced", False)
        for k, v in dict(STATUS_PATH=str(p.path), VERIFY=True, ON_TIMEOUT=1.5, OFF_TIMEOUT=1.5,
                         RETRIES=1, OFF_CONFIRM=1, OFF_CONFIRM_GAP=0.2, WARMUP=0.0,
                         SEPARATE_ON_OFF=False).items():
            monkeypatch.setattr(IR, k, v)
        return p
    return make


def test_turns_on_and_confirms(proj):
    p = proj(on=False)
    assert IR.power_on() and p.on and p.presses == 1


def test_never_toggles_an_already_on_projector(proj):
    """Pi restarts mid-show, the retained 'on' arrives: must NOT press POWER."""
    p = proj(on=True)
    assert IR.power_on() and p.on and p.presses == 0


def test_off_uses_confirm_press(proj):
    p = proj(on=True)
    assert IR.power_off() and not p.on and p.presses == 2


def test_never_toggles_an_already_off_projector(proj):
    p = proj(on=False)
    assert IR.power_off() and not p.on and p.presses == 0


def test_missed_press_is_retried(proj):
    p = proj(on=False, miss=1)
    assert IR.power_on() and p.on and p.presses == 2


def test_gives_up_after_retries(proj):
    p = proj(on=False, miss=99)
    assert IR.power_on() is False and not p.on and p.presses == 2


def test_forced_video_mode_disables_feedback(proj):
    proj(on=True, forced=True)
    assert IR.projector_state() is None


def test_on_then_off_are_serialized(proj):
    p = proj(on=False)
    a = threading.Thread(target=IR.power_on); a.start()
    time.sleep(0.05)
    b = threading.Thread(target=IR.power_off); b.start()
    a.join(); b.join()
    assert not p.on


def test_wait_ignores_wall_clock_steps(monkeypatch):
    """NTP stepping the clock forward mid-wait must not end the wait early:
    that made the retry press POWER again and toggle a booting projector off."""
    calls = {"n": 0}

    def state():
        calls["n"] += 1
        return calls["n"] >= 3               # HDMI comes up on the 3rd check
    real = time.time
    monkeypatch.setattr(IR, "projector_state", state)
    monkeypatch.setattr(time, "time", lambda: real() + 1000 * calls["n"])
    assert IR._wait_for(True, 5) is not None
