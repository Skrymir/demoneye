"""homeassistant/: the dashboard card and alerts work with either tracker, the
camera tracker (camtracker) or the mmWave bridge (ha_bridge.py). Renders the
YAML's templates with stand-ins for HA's template functions, and evaluates the
card's conditional rows, for each combination of trackers running."""
from datetime import datetime, timedelta, timezone

import pytest

yaml = pytest.importorskip("yaml")
jinja2 = pytest.importorskip("jinja2")

CAM = "binary_sensor.camtracker_running"
BRIDGE = "binary_sensor.eye_bridge_running"
NOW = datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc)


def card():
    return yaml.safe_load(open("homeassistant/dashboard_card.yaml"))


def alerts():
    return {a["id"]: a for a in yaml.safe_load(open("homeassistant/alert_automations.yaml"))}


def render(template, states, **variables):
    env = jinja2.Environment()

    def as_datetime(s):
        return datetime.fromisoformat(str(s))
    env.globals.update(
        states=lambda e: states.get(e, "unknown"),
        is_state=lambda e, v: states.get(e, "unknown") == v,
        as_datetime=as_datetime, now=lambda: NOW, timedelta=timedelta)
    return env.from_string(template).render(**variables).strip()


def world(show="on", cam="off", bridge="off", **extra):
    s = {"switch.halloween_eyes_show": show, CAM: cam, BRIDGE: bridge}
    for side in ("left", "right"):          # healthy eyes, so only tracking shows up
        s.update({f"binary_sensor.eye_{side}_running": "on",
                  f"binary_sensor.projector_{side}": "on",
                  f"sensor.eyes_{side}_show_state": "playing"})
    s.update(extra)
    return s


# -- health summary -------------------------------------------------------------
def summary(states):
    return render(card()["cards"][0]["content"], states)


@pytest.mark.parametrize("states, problem", [
    (world(cam="on", **{"binary_sensor.camtracker_stream": "on"}), None),
    (world(bridge="on"), None),
    (world(show="off"), None),                                   # nothing needed at night
    (world(), "no tracker running"),
    (world(cam="on", bridge="on"), "both running"),
    (world(show="off", cam="on", bridge="on"), "both running"),
    (world(cam="on", **{"binary_sensor.camtracker_stream": "off"}), "no picture from the camera"),
])
def test_summary_names_the_tracking_problem(states, problem):
    out = summary(states)
    if problem is None:
        assert "Needs a look" not in out
        assert "all good" in out or "standing by" in out
    else:
        assert "Needs a look" in out and problem in out


# -- tracking rows: whichever tracker is up, both "running" rows if neither -------
def holds(cond, states):
    if cond.get("condition") == "or":
        return any(holds(c, states) for c in cond["conditions"])
    st = states.get(cond["entity"], "unknown")
    return st == cond["state"] if "state" in cond else st != cond["state_not"]


def visible_rows(states):
    (tracking,) = [c for c in card()["cards"] if c.get("title") == "Tracking"]
    out = []
    for row in tracking["entities"]:
        if row.get("type") == "conditional":
            if all(holds(c, states) for c in row["conditions"]):
                out.append(row["row"]["entity"])
        else:
            out.append(row["entity"])
    return out


def test_camera_tracker_rows_only_while_it_runs():
    rows = visible_rows(world(cam="on"))
    assert rows[0] == CAM and BRIDGE not in rows
    assert "binary_sensor.camtracker_tracking" in rows
    assert "sensor.camtracker_last_person_seen" in rows
    assert not any(r in rows for r in ("binary_sensor.tracking_active", "sensor.bridge_host"))


def test_bridge_rows_only_while_it_runs():
    rows = visible_rows(world(bridge="on"))
    assert rows[0] == BRIDGE and CAM not in rows
    assert "binary_sensor.tracking_active" in rows and "sensor.last_target_seen" in rows
    assert not any(r.startswith(("binary_sensor.camtracker", "sensor.camtracker")) for r in rows)


def test_neither_running_shows_both_so_you_see_theyre_down():
    for states in (world(), world(**{CAM: "unknown", BRIDGE: "unknown"})):   # never installed
        assert visible_rows(states) == [CAM, BRIDGE]


def test_both_running_shows_both():
    rows = visible_rows(world(cam="on", bridge="on"))
    assert CAM in rows and BRIDGE in rows


# -- alerts -----------------------------------------------------------------------
def template_conditions_hold(alert, states, **variables):
    out = []
    for c in alert["conditions"]:
        if c["condition"] == "template":
            out.append(render(c["value_template"], states, **variables) == "True")
        elif c["condition"] == "state" and "for" not in c:
            out.append(states.get(c["entity_id"], "unknown") == c["state"])
    return all(out)


@pytest.mark.parametrize("cam, bridge, fires", [
    ("on", "off", False), ("off", "on", False), ("off", "off", True),
    ("unavailable", "off", True), ("on", "on", False)])
def test_tracker_down_alert_needs_both_down(cam, bridge, fires):
    a = alerts()["halloween_eyes_tracker_down"]
    assert template_conditions_hold(a, world(cam=cam, bridge=bridge)) is fires


def test_old_bridge_only_alert_is_gone():
    assert "halloween_eyes_bridge_down" not in alerts()


@pytest.mark.parametrize("cam, bridge, fires", [("on", "on", True), ("on", "off", False),
                                                ("off", "on", False)])
def test_two_trackers_alert(cam, bridge, fires):
    a = alerts()["halloween_eyes_two_trackers"]
    assert template_conditions_hold(a, world(cam=cam, bridge=bridge)) is fires


def last_seen(states):
    return render(alerts()["halloween_eyes_nobody_tracked"]["variables"]["last_seen"], states)


def test_nobody_tracked_uses_the_latest_of_either_tracker():
    recent = (NOW - timedelta(minutes=10)).isoformat()
    old = (NOW - timedelta(days=2)).isoformat()
    a = alerts()["halloween_eyes_nobody_tracked"]
    for states, want, fires in [
        (world(**{"sensor.camtracker_last_person_seen": recent, "sensor.last_target_seen": old}),
         recent, False),
        (world(**{"sensor.camtracker_last_person_seen": old, "sensor.last_target_seen": recent}),
         recent, False),
        (world(**{"sensor.camtracker_last_person_seen": old}), old, True),
        (world(**{"sensor.last_target_seen": "unavailable"}), "", True),        # never seen
    ]:
        seen = last_seen(states)
        assert seen == want
        assert template_conditions_hold(a, states, last_seen=seen) is fires
