"""test_pattern.sh against fake systemctl/sudo/python: it uses the installed
unit's KMSDRM index, brings the eye back with the password-free restart, and
reports a failed pattern instead of hiding it."""
import os
import shutil
import stat
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"),
                                reason="needs bash")

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$LOG"
case "$*" in
  "is-active --quiet demoneye") exit 0 ;;
  "show demoneye -p Environment --value")
    echo "SDL_VIDEODRIVER=kmsdrm EYE_GL=es SDL_KMSDRM_DEVICE_INDEX=$UNIT_IDX" ;;
esac
exit 0
"""
FAKE_SUDO = '#!/usr/bin/env bash\n[ "$1" = -n ] && shift\necho "sudo $*" >> "$LOG"\nexit 0\n'
FAKE_PY = '#!/usr/bin/env bash\necho "$SDL_KMSDRM_DEVICE_INDEX" > "$TMP/idx"\nexit "$PY_EXIT"\n'


def run(tmp_path, py_exit, unit_idx="0"):
    d = tmp_path / "demoneye"
    (d / "venv" / "bin").mkdir(parents=True)
    shutil.copy("test_pattern.sh", d)
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for path, body in ((bin_ / "systemctl", FAKE_SYSTEMCTL), (bin_ / "sudo", FAKE_SUDO),
                       (d / "venv" / "bin" / "python", FAKE_PY)):
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", LOG=str(log),
               TMP=str(tmp_path), PY_EXIT=str(py_exit), UNIT_IDX=unit_idx)
    env.pop("SDL_KMSDRM_DEVICE_INDEX", None)
    r = subprocess.run(["bash", str(d / "test_pattern.sh")], capture_output=True,
                       text=True, env=env, timeout=30)
    return r, log.read_text(), (tmp_path / "idx").read_text().strip()


def test_uses_the_units_kms_index_and_restarts_the_eye(tmp_path):
    r, log, idx = run(tmp_path, py_exit=0, unit_idx="0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert idx == "0", "ignored the installed unit's SDL_KMSDRM_DEVICE_INDEX"
    assert "sudo systemctl stop demoneye" in log
    assert "sudo systemctl restart demoneye" in log      # password-free in sudoers


def test_falls_back_to_index_1(tmp_path):
    _, _, idx = run(tmp_path, py_exit=0, unit_idx="")
    assert idx == "1"


def test_a_failed_pattern_is_reported_and_the_eye_still_comes_back(tmp_path):
    r, log, _ = run(tmp_path, py_exit=1)
    assert r.returncode == 1 and "test pattern failed" in r.stdout
    assert "sudo systemctl restart demoneye" in log


def test_ctrl_c_is_a_normal_exit(tmp_path):
    r, _, _ = run(tmp_path, py_exit=130)
    assert r.returncode == 0
