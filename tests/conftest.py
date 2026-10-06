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
