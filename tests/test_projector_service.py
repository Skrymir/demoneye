"""projector_service.py: the power worker, the HA discovery it sends and its
config loading. The IR/HDMI loop itself is covered by test_ir.py."""
import json
import threading
import time

import pytest

pytest.importorskip("paho.mqtt.client", reason="paho-mqtt not installed")
import projector_service as P


def _render(tmpl, payload):
    jinja2 = pytest.importorskip("jinja2")
    return jinja2.Environment().from_string(tmpl).render(
        value=payload, value_json=json.loads(payload)).strip()


class Recorder:
    def __init__(self, on_ok=True, off_ok=True, on_delay=0.0):
        self.calls, self.on_ok, self.off_ok, self.on_delay = [], on_ok, off_ok, on_delay
        self.results = []

    def on(self):
        self.calls.append("on")
        time.sleep(self.on_delay)
        return self.on_ok

    def off(self):
        self.calls.append("off")
        return self.off_ok


def _worker(rec):
    w = None

    def changed():
        rec.results.append(w.result)
    w = P.PowerWorker(rec.on, rec.off, changed)
    return w


def test_on_reports_turning_then_on():
    rec = Recorder()
    w = _worker(rec)
    w.command(True)
    assert w.run_once()
    assert rec.calls == ["on"]
    assert rec.results == ["turning_on", "on"]
    assert not w.run_once()          # nothing pending


def test_failure_is_reported():
    rec = Recorder(off_ok=False)
    w = _worker(rec)
    w.command(False)
    w.run_once()
    assert rec.results[-1] == "failed_off"


def test_burst_collapses_to_last_command():
    rec = Recorder()
    w = _worker(rec)
    w.command(True)
    w.command(False)
    w.command(True)
    w.run_once()
    assert rec.calls == ["on"]


def test_off_during_slow_on_runs_next_and_on_result_is_not_claimed():
    rec = Recorder(on_delay=0.3)
    w = _worker(rec)
    w.command(True)
    t = threading.Thread(target=w.run_once)
    t.start()
    time.sleep(0.1)
    w.command(False)                 # arrives mid-"on"
    t.join()
    assert rec.results == ["turning_on"]    # "on" not announced: it's stale
    w.run_once()
    assert rec.calls == ["on", "off"]
    assert rec.results[-1] == "off"


def test_stopping_never_presses():
    rec = Recorder()
    w = _worker(rec)
    w.start()
    w.command(True)
    time.sleep(0.2)
    w.stop()
    w._thread.join(2)
    assert rec.calls == ["on"]


def test_repeat_of_done_command_is_ignored():
    """A reconnect replays the retained command: don't press again."""
    rec = Recorder()
    w = _worker(rec)
    assert w.command(True)
    w.run_once()
    assert not w.command(True)
    assert not w.run_once()
    assert rec.calls == ["on"]
    assert w.command(False)          # a real change still goes through


def test_repeat_after_failure_retries():
    rec = Recorder(on_ok=False)
    w = _worker(rec)
    w.command(True)
    w.run_once()
    assert w.result == "failed_on"
    assert w.command(True)
    w.run_once()
    assert rec.calls == ["on", "on"]


def test_discovery_reuses_demoneyes_projector_entity():
    import ha_status
    d = P.discovery("left")
    mine = d["homeassistant/binary_sensor/demoneye_left_projector/config"]
    eye = ha_status.eye_discovery("left")["homeassistant/binary_sensor/demoneye_left_projector/config"]
    # same entity + device as the eye's, so dashboards/alerts carry over
    for k in ("unique_id", "default_entity_id", "device", "device_class", "name"):
        assert mine[k] == eye[k], k
    # but fed by this service
    assert mine["state_topic"] == "house/status/projector_left/state"
    assert mine["availability_topic"] == "house/status/projector_left/availability"
    on = json.dumps({"power": "on", "result": "on", "desired": "on", "changed": "x", "host": "h"})
    unk = json.dumps({"power": "unknown", "result": "idle", "desired": "none", "changed": "x", "host": "h"})
    assert _render(mine["value_template"], on) == "ON"
    assert _render(mine["value_template"], unk) == "None"
    attrs = json.loads(_render(mine["json_attributes_template"], on))
    assert attrs["result"] == "on"
    assert len(d) == 1
    json.dumps(d)


def test_config_node_and_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("MQTT_USER", "envuser")
    monkeypatch.delenv("MQTT_PASS", raising=False)
    ini = tmp_path / "config.ini"
    ini.write_text("[mqtt]\nhost = 10.0.0.9\nusername = fileuser\npassword = p\n"
                   "[projector_service]\nnode = right\n")
    c = P.load(str(ini))
    assert (c["host"], c["user"], c["password"]) == ("10.0.0.9", "envuser", "p")
    assert c["side"] == "right"
    assert c["command_topic"] == "house/christmas/command"


def test_node_auto_from_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(P.ha_status, "hostname", lambda: "pi-eye-left")
    ini = tmp_path / "config.ini"
    ini.write_text("[mqtt]\nhost = x\n")
    assert P.load(str(ini))["side"] == "left"
