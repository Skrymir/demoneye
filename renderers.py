"""
The eye renderer: everything specific to drawing an eye, so the app never
branches on which eye it is.

    schema / lerp   the streamed state keys and their easing
    frag            the shader body (demon / human / walker, from eye_shaders.py)
    make_director() the brain-side behaviour (None on a follower)
    on_target()     how a tracking target reaches the director
    set_uniforms()  push this frame's state to the GPU
    stats_label()   what --stats shows about it

Adding a new eye = one shader body in eye_shaders.py.
"""

from OpenGL import GL

import settings as S
import directors
from eye_shaders import EYE_SHADERS

EYE_FIGURES = sorted(EYE_SHADERS)


class EyeRenderer:
    """One eye per projector (demon / human / walker)."""
    defines = None
    uniforms = ("iResolution", "iTime", "uGaze", "uBlink", "uDilation", "uGlow",
                "uCenter", "uEyeR", "uMirror", "uOpen", "uSlant", "uGazeScale")
    schema = directors.NEUTRAL
    lerp = directors.LERP

    def __init__(self, eye):
        self.name = eye
        self.frag = EYE_SHADERS[eye]
        self.prog = None
        self._uloc = {}

    # -- GL plumbing --
    def bind(self, prog):
        self.prog = prog
        self._uloc = {n: GL.glGetUniformLocation(prog, n) for n in self.uniforms}

    def u1(self, name, x):
        loc = self._uloc.get(name, -1)
        if loc != -1:
            GL.glUniform1f(loc, float(x))

    def u2(self, name, x, y):
        loc = self._uloc.get(name, -1)
        if loc != -1:
            GL.glUniform2f(loc, float(x), float(y))

    # -- behaviour --
    def make_director(self):
        return directors.Director()

    def on_target(self, director, t):
        """t is the parsed target tuple from directors._parse_target, or None."""
        if t and t[0] == "set":
            director.set_target(t[1], t[2], t[3])
        elif t and t[0] == "clear":
            director.clear_target()

    def set_uniforms(self, app, s):
        self.u2("iResolution", app.W, app.H)
        self.u1("iTime", app.t)
        self.u1("uMirror", 1.0 if S.MIRROR else 0.0)
        gx = -s["gx"] if S.MIRROR_GAZE else s["gx"]     # MIRROR_GAZE flips look direction
        self.u2("uGaze", gx, s["gy"])
        self.u1("uBlink", max(0.0, min(1.0, s["blink"])))
        self.u1("uDilation", max(0.0, min(1.0, s["dilation"])))
        self.u1("uGlow", s["glow"])
        # uCenter is a fraction of screen, y measured up (GL); config y is top-down
        self.u2("uCenter", S.EYE_CENTER_X, 1.0 - S.EYE_CENTER_Y)
        self.u1("uEyeR", app.H * 0.42 * S.EYE_SCALE)
        self.u1("uOpen", max(0.0, min(1.6, s["open"])))
        self.u1("uSlant", max(-1.0, min(1.0, s["slant"])))
        self.u2("uGazeScale", S.GAZE_SCALE_X, S.GAZE_SCALE_Y)

    def stats_label(self, app, s):
        return f"[{app.director.mood}]" if app.director else ""


def make_renderer(eye):
    """The renderer for --eye. Raises ValueError with a clear message."""
    if eye not in EYE_SHADERS:
        raise ValueError(f"--eye must be one of {EYE_FIGURES} (got {eye!r})")
    return EyeRenderer(eye)
