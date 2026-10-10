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


def test_rsync_keeps_pi_only_files_and_never_ships_secrets(tmp_path):
    """Real rsync into a fake Pi folder: --delete removes stale code, but
    backups and local/ survive, and .env / secrets/ never leave this Mac."""
    real_rsync = shutil.which("rsync")
    if not real_rsync:
        pytest.skip("rsync not installed")
    src, pi = tmp_path / "src", tmp_path / "pi" / "demoneye"
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                           check=True).stdout.split()
    for f in files:
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, src / f)
    (src / ".env").write_text("HA_TOKEN=mac-secret\n")
    (src / "secrets").mkdir()
    (src / "secrets" / "key").write_text("mac-secret\n")

    pi.mkdir(parents=True)
    for name, text in {"config.ini": "pi config\n", "config.ini.bak": "backup\n",
                       "local/notes.txt": "keep me\n", ".env": "MQTT_PASS=pi\n",
                       "old_module.py": "stale\n"}.items():
        (pi / name).parent.mkdir(parents=True, exist_ok=True)
        (pi / name).write_text(text)

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    scripts = {
        "ssh": FAKE_SSH,
        "sleep": "#!/usr/bin/env bash\nexit 0\n",
        # user@host:dir/ -> the fake Pi's home, then the real rsync
        "rsync": '#!/usr/bin/env bash\nargs=()\nfor a in "$@"; do\n'
                 '  if [[ "$a" == *@*:* ]]; then a="$PI_HOME/${a#*:}"; fi\n'
                 '  args+=("$a")\ndone\nexec "$REAL_RSYNC" "${args[@]}"\n',
    }
    for name, body in scripts.items():
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}",
               LOG=str(tmp_path / "ssh.log"), FAIL_PIP="__none__",
               PI_HOME=str(tmp_path / "pi"), REAL_RSYNC=real_rsync)
    r = subprocess.run(["bash", str(src / "deploy.sh"), "--skip-tests", "--no-restart", "pi-a"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr

    assert (pi / "demoneye.py").exists(), "code wasn't copied"
    assert not (pi / "old_module.py").exists(), "stale file wasn't removed"
    assert (pi / "config.ini").read_text() == "pi config\n"
    assert (pi / "config.ini.bak").read_text() == "backup\n"
    assert (pi / "local" / "notes.txt").read_text() == "keep me\n"
    assert (pi / ".env").read_text() == "MQTT_PASS=pi\n", "the Mac's .env was shipped"
    assert not (pi / "secrets").exists(), "secrets/ was shipped"


def _project_copy(tmp_path, venv_python):
    """This project in a temp folder, with a fake venv/bin/python."""
    src = tmp_path / "src"
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                           check=True).stdout.split()
    for f in files:
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, src / f)
    py = src / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(venv_python)
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    return src


def _deploy(src, tmp_path):
    log = tmp_path / "ssh.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in (("ssh", FAKE_SSH), ("rsync", "#!/usr/bin/env bash\nexit 0\n"),
                       ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
        (bin_ / name).write_text(body)        # nothing here may reach a real host
        (bin_ / name).chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}",
               LOG=str(log), FAIL_PIP="__none__", TMP=str(tmp_path))
    r = subprocess.run(["bash", str(src / "deploy.sh"), "pi-a"],
                       capture_output=True, text=True, env=env, timeout=30)
    return r, log.read_text() if log.exists() else ""


def test_no_pytest_means_no_deploy(tmp_path):
    src = _project_copy(tmp_path, "#!/usr/bin/env bash\n"
                        '[[ "$*" == *"import pytest"* ]] && exit 1\nexit 0\n')
    r, ssh_log = _deploy(src, tmp_path)
    assert r.returncode == 1
    assert "pytest isn't installed" in r.stdout
    assert ssh_log == "", "deployed without running the tests"


def test_tests_run_in_missing_dependency_mode(tmp_path):
    """deploy.sh runs pytest with DEMONEYE_NO_SKIPS=deps and stops if it fails."""
    src = _project_copy(tmp_path, "#!/usr/bin/env bash\n"
                        '[[ "$*" == *"import pytest"* ]] && exit 0\n'
                        'echo "$DEMONEYE_NO_SKIPS" > "$TMP/mode"; exit 1\n')
    r, ssh_log = _deploy(src, tmp_path)
    assert (tmp_path / "mode").read_text().strip() == "deps"
    assert r.returncode == 1 and "not deploying" in r.stdout
    assert ssh_log == ""


CONFTEST = os.path.join(os.path.dirname(__file__), "conftest.py")
SKIPPING_TESTS = '''
import pytest

def test_missing_dep():
    pytest.importorskip("no_such_module_xyz")

def test_mac_only_skip():
    pytest.skip("headless GL (EGL) is Linux-only")

def test_ok():
    pass
'''


@pytest.mark.parametrize("mode, extra_test, want", [
    ("deps", False, 0),      # only the expected platform skip: fine
    ("deps", True, 1),       # a missing dependency: fail
    ("1", False, 1),         # CI: any skip fails
    ("", True, 0),           # unset: skips are just skips
])
def test_no_skips_modes(tmp_path, mode, extra_test, want):
    d = tmp_path / "tests"
    d.mkdir()
    shutil.copy(CONFTEST, d / "conftest.py")
    body = SKIPPING_TESTS if extra_test else SKIPPING_TESTS.replace("def test_missing_dep", "def _off")
    (d / "test_x.py").write_text(body)
    env = dict(os.environ, DEMONEYE_NO_SKIPS=mode)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d)],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == want, r.stdout
