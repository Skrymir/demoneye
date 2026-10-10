"""Dependency pins: every requirement is bounded below the next major
version, constraints.txt pins an exact version inside every range, and this
venv runs exactly the pinned runtime versions (the ones the Pis get)."""
import re
from importlib import metadata
from pathlib import Path

import pytest

packaging = pytest.importorskip("packaging")
from packaging.requirements import Requirement  # noqa: E402
from packaging.utils import canonicalize_name  # noqa: E402

REQ_FILES = ["requirements.txt", "requirements-bridge.txt", "requirements-dev.txt"]
RUNTIME = ["requirements.txt", "requirements-bridge.txt"]


def reqs(path):
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.append(Requirement(line))
    return out


def pins():
    out = {}
    for r in reqs("constraints.txt"):
        (spec,) = r.specifier
        assert spec.operator == "==", f"constraints.txt must pin exactly: {r}"
        out[canonicalize_name(r.name)] = spec.version
    return out


@pytest.mark.parametrize("path", REQ_FILES)
def test_every_requirement_has_an_upper_bound(path):
    for r in reqs(path):
        assert any(s.operator in ("<", "<=", "~=", "==") for s in r.specifier), \
            f"{path}: {r} has no upper bound — the next major could break us"


@pytest.mark.parametrize("path", REQ_FILES)
def test_constraints_pin_every_requirement_within_its_range(path):
    p = pins()
    for r in reqs(path):
        name = canonicalize_name(r.name)
        assert name in p, f"{r.name} isn't pinned in constraints.txt"
        assert r.specifier.contains(p[name]), \
            f"constraints.txt pins {r.name}=={p[name]}, outside {path}'s {r.specifier}"


def test_this_venv_runs_the_pinned_runtime_versions():
    """The tests only vouch for what the Pis run if this venv matches the pins."""
    p = pins()
    wrong = []
    for path in RUNTIME:
        for r in reqs(path):
            name = canonicalize_name(r.name)
            have = metadata.version(r.name)
            if have != p[name]:
                wrong.append(f"{r.name} {have} (pinned {p[name]})")
    assert not wrong, ("venv differs from constraints.txt: " + ", ".join(wrong) +
                       " — ./venv/bin/pip install -r requirements-dev.txt -c constraints.txt")


@pytest.mark.parametrize("path", ["deploy.sh", ".github/workflows/tests.yml"])
def test_installs_use_the_constraints(path):
    for line in Path(path).read_text().splitlines():
        if re.search(r"pip install .*-r requirements", line):
            assert "-c constraints.txt" in line, f"{path}: {line.strip()}"
