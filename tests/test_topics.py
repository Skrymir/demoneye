"""The MQTT topics are a contract between the eyes, the bridge, Home
Assistant, Node-RED and the Godot windows. Every copy of a topic name in this
repo must agree, so a rename can't leave one behind."""
import configparser
import json

import pytest

import ha_status
import settings as S

COMMAND = "house/show/command"
TARGET = "house/tracking/target"
STATUS = "house/status"
STATE = "house/eyes/state"


def ini(path):
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read(path)
    return cp


def test_eye_defaults():
    import importlib
    fresh = importlib.reload(S)            # module defaults, before any config.ini
    assert (fresh.COMMAND_TOPIC, fresh.TARGET_TOPIC, fresh.STATUS_BASE_TOPIC, fresh.TOPIC) \
        == (COMMAND, TARGET, STATUS, STATE)
    S.load_config("config.ini")


def test_config_ini_template():
    cp = ini("config.ini")
    assert cp.get("mqtt", "command_topic") == COMMAND
    assert cp.get("mqtt", "target_topic") == TARGET
    assert cp.get("mqtt", "topic") == STATE
    assert cp.get("status", "base_topic") == STATUS


def test_bridge():
    pytest.importorskip("websockets")
    pytest.importorskip("paho.mqtt")
    import ha_bridge
    cfg = ha_bridge.Config(None)
    assert (cfg.command_topic, cfg.topic) == (COMMAND, TARGET)
    cp = ini("bridge.ini")
    assert cp.get("mqtt", "command_topic") == COMMAND
    assert cp.get("mqtt", "target_topic") == TARGET
    assert cp.get("status", "base_topic") == STATUS


def test_status_defaults():
    assert ha_status.STATUS_DEFAULTS["base_topic"] == STATUS


def test_check_config_defaults():
    import check_config as CC
    assert CC.BRIDGE_DEFAULTS["command_topic"] == COMMAND
    assert CC.BRIDGE_DEFAULTS["target_topic"] == TARGET
    assert CC.MQTT_DEFAULTS["command_topic"] == COMMAND


def test_track_test_defaults():
    pytest.importorskip("paho.mqtt")
    import track_test
    cp = track_test.load_cfg(None)
    assert (cp.get("mqtt", "command_topic"), cp.get("mqtt", "target_topic"),
            cp.get("mqtt", "topic")) == (COMMAND, TARGET, STATE)


def test_node_red_flow():
    flow = json.load(open("homeassistant/nodered_flow.json"))
    topics = {n["topic"] for n in flow if n.get("type", "").startswith("mqtt") and n.get("topic")}
    assert topics == {COMMAND}, topics
