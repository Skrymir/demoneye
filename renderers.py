"""
Renderers — one class per thing we can put on a projector.

Each renderer owns everything specific to its figure type, so the app never
branches on what it is drawing:

    schema / lerp   the streamed state keys and their easing
    frag / defines  the shader body and the #defines injected into it
    uniforms        the uniform names it sets
    make_director() the brain-side behaviour (None on a follower)
    on_target()     how a tracking target reaches its director
    after_smooth()  per-frame bookkeeping (e.g. spirit trail history)
    set_uniforms()  push this frame's state to the GPU
    stats_label()   what --stats shows about it

Adding a new figure = one class here + one shader body.
"""

import math

from OpenGL import GL

import settings as S
import directors
import stage
from eye_shaders import EYE_SHADERS
from stage_shaders import STAGE_SHADERS
from glutil import load_atlas


class Renderer:
    name = "renderer"
    frag = ""
    defines = None
    uniforms = ()
    schema = {}
    lerp = {}

    def __init__(self):
        self.prog = None
        self._uloc = {}

    # -- GL plumbing shared by every renderer --
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

    def u1v(self, name, vals):
        loc = self._uloc.get(name, -1)
        if loc != -1:
            n = len(vals)
            GL.glUniform1fv(loc, n, (GL.GLfloat * n)(*[float(v) for v in vals]))

    def common(self, app):
        self.u2("iResolution", app.W, app.H)
        self.u1("iTime", app.t)
        self.u1("uMirror", 1.0 if S.MIRROR else 0.0)

    # -- hooks with sensible defaults --
    def make_director(self):
        return None

    def on_target(self, director, t):
        """t is the parsed target tuple from directors._parse_target, or None."""

    def after_smooth(self, app, dt):
        pass

    def set_uniforms(self, app, s):
        raise NotImplementedError

    def stats_label(self, app, s):
        return ""


# ---------------------------------------------------------------------------
class EyeRenderer(Renderer):
    """One eye per projector (demon / human / walker)."""
    uniforms = ("iResolution", "iTime", "uGaze", "uBlink", "uDilation", "uGlow",
                "uCenter", "uEyeR", "uMirror", "uOpen", "uSlant", "uGazeScale")
    schema = directors.NEUTRAL
    lerp = directors.LERP

    def __init__(self, eye):
        super().__init__()
        self.name = eye
        self.frag = EYE_SHADERS[eye]

    def make_director(self):
        return directors.Director()

    def on_target(self, director, t):
        if t and t[0] == "set":
            director.set_target(t[1], t[2], t[3])
        elif t and t[0] == "clear":
            director.clear_target()

    def set_uniforms(self, app, s):
        self.common(app)
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


# ---------------------------------------------------------------------------
class _StageRenderer(Renderer):
    """Shared by every stage-mode renderer: the window's slice of the stage."""
    @staticmethod
    def span():
        return max(1e-6, S.STAGE_TO - S.STAGE_FROM)

    def local(self, x):
        return (x - S.STAGE_FROM) / self.span()

    def on_target(self, director, t):
        # stage figures don't aim at people; they react to being watched
        director.set_watched(bool(t and t[0] == "set"))


class FigureRenderer(_StageRenderer):
    """A single figure on the stage: silhouette, sprite, or ghost."""
    uniforms = ("iResolution", "iTime", "uLocal", "uFacing", "uPhase", "uLit",
                "uFigScale", "uMirror", "uDepth", "uYaw",
                "uFrames", "uCols", "uRows", "uCellAspect", "uSrcFacing", "uAtlas")
    schema = stage.STAGE_NEUTRAL
    lerp = stage.STAGE_LERP

    def __init__(self, kind):
        super().__init__()
        self.name = kind
        self.frag = STAGE_SHADERS[kind]
        self.atlas = None

    def bind(self, prog):
        super().bind(prog)
        if self.name == "sprite":
            self.atlas = load_atlas(S.FIGURE_ATLAS)
            if self.atlas is None:
                raise SystemExit(f"[fatal] figure_atlas set but could not be loaded: {S.FIGURE_ATLAS}")

    def make_director(self):
        if self.name == "ghost":
            return stage.DriftDirector(speed=S.STAGE_SPEED,
                                       freeze_when_watched=S.STAGE_FREEZE_WHEN_WATCHED)
        return stage.StageDirector(speed=S.STAGE_SPEED, stride=S.FIGURE_STRIDE,
                                   pause_windows=(S.STAGE_FROM + (S.STAGE_TO - S.STAGE_FROM) * 0.5,),
                                   freeze_when_watched=S.STAGE_FREEZE_WHEN_WATCHED)

    def set_uniforms(self, app, s):
        self.common(app)
        # <0 or >1 means the figure is beyond this window's edges (behind the wall)
        self.u1("uLocal", self.local(s["pos"]))
        self.u1("uFacing", 1.0 if s["facing"] >= 0 else -1.0)
        self.u1("uPhase", s["phase"])
        self.u1("uLit", s["lit"])
        self.u1("uFigScale", S.FIG_SCALE)
        self.u1("uDepth", s.get("depth", 0.0))
        self.u1("uYaw", math.atan2(s.get("yawy", 0.0), s.get("yawx", 1.0)))
        if self.atlas:
            tex, meta = self.atlas
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            loc = self._uloc.get("uAtlas", -1)
            if loc != -1:
                GL.glUniform1i(loc, 0)
            self.u1("uFrames", meta["frames"])
            self.u1("uCols", meta["cols"])
            self.u1("uRows", meta["rows"])
            self.u1("uCellAspect", meta["cell_w"] / max(1.0, float(meta["cell_h"])))
            self.u1("uSrcFacing", -1.0 if S.FIGURE_FACES.startswith("l") else 1.0)

    def stats_label(self, app, s):
        return f"[pos {s['pos']:.2f}]"


class _MultiRenderer(_StageRenderer):
    """Several figures streamed in numbered slots (pos0, pos1, ...)."""
    slots = 1

    def visible(self, s):
        return sum(1 for i in range(self.slots) if s[f"lit{i}"] > 0.05)


class BallroomRenderer(_MultiRenderer):
    """The Haunted-Mansion ballroom: SLOTS Victorian ghosts waltzing at depth."""
    name = "mansion"
    frag = STAGE_SHADERS["mansion"]
    slots = stage.BALLROOM_SLOTS
    defines = {"SLOTS": stage.BALLROOM_SLOTS}
    uniforms = ("iResolution", "iTime", "uFigScale", "uMirror",
                "uPos", "uDepth", "uYaw", "uPhase", "uLit")
    schema = stage.BALLROOM_NEUTRAL
    lerp = stage.BALLROOM_LERP

    def make_director(self):
        return stage.BallroomDirector(speed=S.STAGE_SPEED,
                                      freeze_when_watched=S.STAGE_FREEZE_WHEN_WATCHED)

    def set_uniforms(self, app, s):
        self.common(app)
        self.u1("uFigScale", S.FIG_SCALE)
        order = sorted(range(self.slots), key=lambda i: -s[f"depth{i}"])   # far first
        self.u1v("uPos", [self.local(s[f"pos{i}"]) for i in order])
        self.u1v("uDepth", [s[f"depth{i}"] for i in order])
        self.u1v("uYaw", [math.atan2(s[f"yawy{i}"], s[f"yawx{i}"]) for i in order])
        self.u1v("uPhase", [s[f"phase{i}"] for i in order])
        self.u1v("uLit", [max(0.0, min(1.0, s[f"lit{i}"])) for i in order])

    def stats_label(self, app, s):
        return f"[{self.visible(s)} dancers]"


class SpiritsRenderer(_MultiRenderer):
    """SLOTS spirit lights in 3D; trails drawn through a history of positions."""
    name = "spirits"
    frag = STAGE_SHADERS["spirits"]
    slots = stage.SPIRIT_SLOTS
    defines = {"SLOTS": stage.SPIRIT_SLOTS, "NH": S.SPIRIT_HIST_N}
    uniforms = ("iResolution", "iTime", "uFigScale", "uMirror",
                "uX", "uY", "uZ", "uLit", "uHX", "uHY", "uHZ")
    schema = stage.SPIRIT_NEUTRAL
    lerp = stage.SPIRIT_LERP

    def __init__(self):
        super().__init__()
        self._hist = [[(0.5, 0.5, 0.5)] * S.SPIRIT_HIST_N for _ in range(self.slots)]
        self._hist_t = 0.0

    def make_director(self):
        return stage.SpiritDirector(speed=S.STAGE_SPEED,
                                    freeze_when_watched=S.STAGE_FREEZE_WHEN_WATCHED)

    def after_smooth(self, app, dt):
        """Sample each spirit's displayed position at a fixed interval so the
        trail has a consistent length whatever the frame rate. A teleport
        (blink) resets the trail instead of smearing a line across the room."""
        self._hist_t += dt
        if self._hist_t < S.SPIRIT_HIST_DT:
            return
        self._hist_t = 0.0
        s = app.disp
        for i, h in enumerate(self._hist):
            p = (s[f"x{i}"], s[f"y{i}"], s[f"z{i}"])
            last = h[-1]
            jump = math.dist(p, last)
            if jump > 0.22 or s[f"lit{i}"] < 0.02:
                self._hist[i] = [p] * S.SPIRIT_HIST_N
            else:
                h.append(p)
                del h[0]

    def set_uniforms(self, app, s):
        self.common(app)
        self.u1("uFigScale", S.FIG_SCALE)
        n = self.slots
        self.u1v("uX", [self.local(s[f"x{i}"]) for i in range(n)])
        self.u1v("uY", [s[f"y{i}"] for i in range(n)])
        self.u1v("uZ", [s[f"z{i}"] for i in range(n)])
        self.u1v("uLit", [max(0.0, min(1.0, s[f"lit{i}"])) for i in range(n)])
        hx, hy, hz = [], [], []
        for i in range(n):
            for (px, py, pz) in self._hist[i]:
                hx.append(self.local(px)); hy.append(py); hz.append(pz)
        self.u1v("uHX", hx); self.u1v("uHY", hy); self.u1v("uHZ", hz)

    def stats_label(self, app, s):
        return f"[{self.visible(s)} spirits]"


# ---------------------------------------------------------------------------
def make_renderer(mode, figure):
    """Pick the renderer for --mode/--eye. Raises ValueError with a clear message."""
    if mode == "eyes":
        if figure not in EYE_SHADERS:
            raise ValueError(f"--eye must be one of {sorted(EYE_SHADERS)} in eyes mode")
        return EyeRenderer(figure)
    # stage: an explicit figure wins; else a configured atlas selects sprites;
    # else the procedural silhouette.
    if figure in ("mansion",):
        return BallroomRenderer()
    if figure in ("spirits",):
        return SpiritsRenderer()
    if figure in ("silhouette", "sprite", "ghost"):
        return FigureRenderer(figure)
    if figure in EYE_SHADERS:            # default --eye demon in stage mode
        return FigureRenderer("sprite" if S.FIGURE_ATLAS else "silhouette")
    raise ValueError(f"unknown stage figure {figure!r}; choose one of "
                     f"{sorted(STAGE_SHADERS)}")


STAGE_FIGURES = sorted(STAGE_SHADERS)
EYE_FIGURES = sorted(EYE_SHADERS)
