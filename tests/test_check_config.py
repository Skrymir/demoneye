"""check_config's unit parsing and pair rules: one brain, the same eye, the
same broker and topics, mirror on one Pi, one bridge."""
import configparser

import pytest

import check_config as CC


def cp(text=""):
    c = configparser.ConfigParser()
    c.read_string(text)
    return c


UNIT = """[Service]
Environment=SDL_KMSDRM_DEVICE_INDEX=1
ExecStart=/home/joshc/demoneye/venv/bin/python /home/joshc/demoneye/demoneye.py {args}
"""


@pytest.mark.parametrize("args, role, eye", [
    ("--role brain", "brain", "walker"),                  # walker is the default
    ("--role follower --eye demon", "follower", "demon"),
    ("--role=brain --eye=human", "brain", "human"),       # argparse accepts both forms
    ("--eye demon", None, "demon"),
])
def test_parse_service(args, role, eye):
    svc = CC.parse_service(UNIT.format(args=args))
    assert (svc["role"], svc["eye"], svc["kms_index"], svc["found"]) == (role, eye, 1, True)


def svc(role, eye="walker", bridge=None):
    s = {"role": role, "eye": eye, "found": True}
    if bridge is not None:
        s["bridge"] = {"runs": bridge}
    return s


LEFT = cp("[display]\nmirror = true\n[status]\nnode = left\n")
RIGHT = cp("[display]\nmirror = false\n[status]\nnode = right\n")


def pair(left_svc, right_svc, left=LEFT, right=RIGHT):
    return CC.check_pair([("pi-eye-left.home", left, left_svc),
                          ("pi-eye-right.home", right, right_svc)])


def msgs(rep, tag):
    return [m for t, m in rep.lines if t == tag]


def test_a_good_pair_has_no_errors():
    rep = pair(svc("brain", bridge=False), svc("follower", bridge=True))
    assert rep.errors == 0 and rep.warnings == 0, rep.lines


@pytest.mark.parametrize("roles, needle", [
    (("follower", "follower"), "no Pi is the brain"),
    (("brain", "brain"), "more than one brain"),
])
def test_brain_count(roles, needle):
    rep = pair(svc(roles[0]), svc(roles[1]))
    assert any(needle in m for m in msgs(rep, CC.ERR)), rep.lines


def test_different_eyes():
    rep = pair(svc("brain", "walker"), svc("follower", "demon"))
    assert any("different eyes" in m for m in msgs(rep, CC.ERR))


def test_two_bridges():
    rep = pair(svc("brain", bridge=True), svc("follower", bridge=True))
    assert any("tracking bridge runs on" in m for m in msgs(rep, CC.ERR))


def test_mirror_on_both_warns():
    rep = pair(svc("brain"), svc("follower"), right=cp("[display]\nmirror = true\n[status]\nnode = right\n"))
    assert any("mirror = true on both" in m for m in msgs(rep, CC.WARN))


def test_mqtt_default_matches_explicit_default():
    """One Pi leaving a key out and the other setting it to the default value
    is the same setting — not 'topic differs'."""
    explicit = cp(f"[mqtt]\ntopic = {CC.MQTT_DEFAULTS['topic']}\nport = 1883\n"
                  "[display]\nmirror = false\n[status]\nnode = right\n")
    rep = pair(svc("brain"), svc("follower"), right=explicit)
    assert rep.errors == 0, rep.lines


def test_mqtt_really_different_is_an_error():
    other = cp("[mqtt]\ntopic = somewhere/else\n[display]\nmirror = false\n[status]\nnode = right\n")
    rep = pair(svc("brain"), svc("follower"), right=other)
    assert any("topic differs" in m for m in msgs(rep, CC.ERR))
