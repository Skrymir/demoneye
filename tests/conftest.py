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


# A skip because something isn't installed, vs. one that's expected on this
# machine (the GL draw tests on a Mac).
_MISSING = ("could not import", "not installed")


def _skip_reason(report):
    lr = report.longrepr
    return lr[2] if isinstance(lr, tuple) and len(lr) == 3 else str(lr)


def pytest_sessionfinish(session, exitstatus):
    """DEMONEYE_NO_SKIPS=1 (CI): any skipped test fails the run.
    DEMONEYE_NO_SKIPS=deps (deploy.sh): only skips for a missing dependency or
    tool fail it, so a half-installed venv can't quietly test less."""
    mode = os.environ.get("DEMONEYE_NO_SKIPS")
    if not mode:
        return
    rep = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = rep.stats.get("skipped", []) if rep else []
    if mode == "deps":
        skipped = [r for r in skipped if any(m in _skip_reason(r) for m in _MISSING)]
    if skipped:
        rep.ensure_newline()
        what = "skipped for a missing dependency" if mode == "deps" else "skipped"
        rep.write_line(f"DEMONEYE_NO_SKIPS: {len(skipped)} test(s) {what} — failing", red=True)
        for reason in sorted({_skip_reason(r) for r in skipped}):
            rep.write_line(f"  {reason}", red=True)
        session.exitstatus = 1
