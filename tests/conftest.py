import os, sys

# Headless rendering for the draw-a-frame tests uses EGL + SDL's offscreen
# driver — Linux only (the Pi, CI). macOS has no EGL, and forcing
# PYOPENGL_PLATFORM=egl there breaks `import OpenGL` for every test, so on a
# Mac we leave PyOpenGL alone and test_renderers skips itself.
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
    os.environ.setdefault("EYE_GL", "es")
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def pytest_sessionfinish(session, exitstatus):
    """CI sets DEMONEYE_NO_SKIPS: any skipped test fails the run, so a missing
    dependency or GL driver can't silently shrink what's tested."""
    if not os.environ.get("DEMONEYE_NO_SKIPS"):
        return
    rep = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = rep.stats.get("skipped", []) if rep else []
    if skipped:
        rep.ensure_newline()
        rep.write_line(f"DEMONEYE_NO_SKIPS: {len(skipped)} test(s) skipped — failing", red=True)
        session.exitstatus = 1
