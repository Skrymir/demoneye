"""deploy.sh against fake ssh/rsync: one Pi failing to install requirements
must not stop the deploy to the others or hide the summary."""
import os
import shutil
import stat
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"),
                                reason="needs bash")

FAKE_SSH = """#!/usr/bin/env bash
# args: [-o opt]... [-t] user@host command
while [[ "$1" == -* ]]; do [[ "$1" == -o ]] && shift; shift; done
host="$1"; shift
echo "$host $*" >> "$LOG"
if [[ "$*" == *"pip install"* && "$host" == *"$FAIL_PIP"* ]]; then
  echo "ERROR: Could not find a version that satisfies the requirement" >&2; exit 1
fi
[[ "$*" == "systemctl is-active --quiet ha-bridge" ]] && exit 3   # no bridge here
exit 0
"""


@pytest.fixture
def fakes(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("rsync", "#!/usr/bin/env bash\nexit 0\n"),
                       ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "ssh.log"
    log.touch()

    def run(*hosts, fail_pip=""):
        env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}",
                   LOG=str(log), FAIL_PIP=fail_pip or "__none__")
        r = subprocess.run(["bash", "deploy.sh", "--skip-tests", *hosts],
                           capture_output=True, text=True, env=env, timeout=30)
        return r, log.read_text()
    return run


def test_deploys_both_and_installs_bridge_requirements(fakes):
    r, log = fakes("pi-a", "pi-b")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "deployed to: pi-a pi-b" in r.stdout
    pip = [l for l in log.splitlines() if "pip install" in l]
    assert len(pip) == 2 and all("requirements-bridge.txt" in l for l in pip)


def test_pip_failure_skips_that_pi_but_not_the_rest(fakes):
    r, log = fakes("pi-a", "pi-b", fail_pip="pi-a")
    assert r.returncode == 1
    assert "problems with: pi-a" in r.stdout          # summary still printed
    assert "joshc@pi-a sudo -n systemctl restart demoneye" not in log, \
        "restarted a Pi whose requirements failed to install"
    assert "joshc@pi-b sudo -n systemctl restart demoneye" in log, \
        "stopped before deploying to the second Pi"
