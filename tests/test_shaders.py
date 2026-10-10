"""Every shader must compile on BOTH GL profiles the project targets:
GLSL ES 3.00 (the Pi) and desktop GLSL 3.30 (the Mac). Desktop GLSL reserves
names like noise1..noise4 that GLES tolerates — this test caught exactly that."""
import shutil, subprocess
import pytest
from glsl_common import assemble, VERT
from eye_shaders import EYE_SHADERS

from christmas import CHRISTMAS_FRAG

ALL = dict(EYE_SHADERS, christmas=CHRISTMAS_FRAG)
glslang = shutil.which("glslangValidator")


def _validate(src, suffix, tmp_path):
    p = tmp_path / f"s.{suffix}"
    p.write_text(src)
    r = subprocess.run([glslang, str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.skipif(not glslang, reason="glslangValidator not installed")
@pytest.mark.parametrize("gles", [True, False], ids=["ES300", "GL330"])
@pytest.mark.parametrize("name", sorted(ALL))
def test_fragment_compiles(name, gles, tmp_path):
    _validate(assemble(ALL[name], "frag", gles), "frag", tmp_path)


@pytest.mark.skipif(not glslang, reason="glslangValidator not installed")
@pytest.mark.parametrize("gles", [True, False], ids=["ES300", "GL330"])
def test_vertex_compiles(gles, tmp_path):
    _validate(assemble(VERT, "vert", gles), "vert", tmp_path)


def test_no_duplicated_helpers():
    """Helpers live in the prelude only; a body redefining one would shadow it."""
    import re
    for name, body in ALL.items():
        for fn in ("hash21", "vnoise", "fbm", "voro"):
            assert not re.search(rf"^\s*float\s+{fn}\s*\(", body, re.M), (name, fn)
        assert "#version" not in body, name

