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


def test_discovery_switch_and_result_templates():
    d = P.discovery("left", "house/projector/left/command")
    sw = d["homeassistant/switch/demoneye_projector_left_power/config"]
    assert sw["command_topic"] == "house/projector/left/command"
    assert sw["state_topic"] == "house/status/projector_left/state"
    assert sw["availability_topic"] == "house/status/projector_left/availability"
    # its own device, so the eye's (offline in FPP season) entities aren't touched
    assert sw["device"]["identifiers"] == ["demoneye_projector_left"]
    on = json.dumps({"power": "on", "result": "on"})
    unk = json.dumps({"power": "unknown", "result": "idle"})
    assert _render(sw["value_template"], on) == "on"
    assert _render(sw["value_template"], unk) == "None"
    res = d["homeassistant/sensor/demoneye_projector_left_result/config"]
    assert _render(res["value_template"], on) in res["options"]
    for cfg in d.values():
        json.dumps(cfg)


def test_config_node_and_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("MQTT_USER", "envuser")
    monkeypatch.delenv("MQTT_PASS", raising=False)
    ini = tmp_path / "config.ini"
    ini.write_text("[mqtt]\nhost = 10.0.0.9\nusername = fileuser\npassword = p\n"
                   "[projector_service]\nnode = right\n")
    c = P.load(str(ini))
    assert (c["host"], c["user"], c["password"]) == ("10.0.0.9", "envuser", "p")
    assert c["side"] == "right"
    assert c["command_topic"] == "house/projector/command"
    assert c["node_command_topic"] == "house/projector/right/command"


def test_node_auto_from_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(P.ha_status, "hostname", lambda: "pi-eye-left")
    ini = tmp_path / "config.ini"
    ini.write_text("[mqtt]\nhost = x\n")
    assert P.load(str(ini))["side"] == "left"
