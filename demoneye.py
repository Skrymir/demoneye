#!/usr/bin/env python3
"""
Demon Eye — synchronized Halloween projection.

Two Raspberry Pis, one projector each, projecting a glowing red demon eye onto
a house. One Pi is the "brain" (--role brain): it directs the animation and
streams the eye state over MQTT ~50x/sec. Both Pis render that state, easing
smoothly toward it every frame, so the two eyes move as one.

Run:
    python demoneye.py --role brain      # exactly ONE Pi
    python demoneye.py --role follower   # the other Pi
    python demoneye.py --role brain --windowed   # for testing on a desktop

Everything you'll want to tune lives in the CONFIG block below.
"""

import argparse
import configparser
import json
import math
import os
import random
import re
import signal
import sys
import time

import numpy as np
import pygame

# PyOpenGL and SDL must agree on how they reach the GL stack. On Linux — the Pi
# console (KMSDRM), Wayland, or X11 — Mesa is reached through EGL, so pin both to
# EGL: PyOpenGL uses the EGL platform, and SDL is told to use EGL on X11 too
# (it defaults to GLX there). Otherwise PyOpenGL looks via GLX and can't find the
# context SDL made through EGL ("no valid context"). macOS/Windows keep defaults.
if sys.platform.startswith("linux"):
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    os.environ.setdefault("SDL_VIDEO_X11_FORCE_EGL", "1")
from OpenGL import GL

from eye_shaders import VERT, EYE_SHADERS

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # allowed in --windowed --solo test mode

try:
    import ir_control
except ImportError:
    ir_control = None   # projector IR control is optional

try:
    import stats as stats_mod
except ImportError:
    stats_mod = None    # --stats is optional

try:
    import stage as stage_mod
    from stage_shaders import VERT as STAGE_VERT, STAGE_SHADERS
except ImportError:
    stage_mod = None    # stage mode is optional

# ============================================================================
# CONFIG
# ============================================================================
# NOTE: the per-machine settings below (MQTT connection + eye placement +
# mirror flags) are the DEFAULTS. They are overridden at startup by config.ini
# (next to this script) — edit that file, not this block, so updating the
# script never clobbers your broker address or alignment. Everything else here
# (the look and animation) is shared across both Pis and lives in the script.

# --- MQTT (defaults; real values live in config.ini [mqtt]) ---
MQTT_HOST = "192.168.1.10"          # your broker (Home Assistant / Mosquitto)
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
TOPIC     = "halloween/eyes/state"
COMMAND_TOPIC = "halloween/eyes/command"   # Home Assistant publishes on/off here
TARGET_TOPIC  = "halloween/eyes/target"    # a sensor bridge publishes look-here targets
PUBLISH_HZ = 50                     # how often the brain streams state

# --- Playback control ---
# START_MODE: "playing" renders as soon as the app starts; "idle" shows black
# and waits for an "on" command on COMMAND_TOPIC. Either way the app always
# listens, so Home Assistant can start/stop it at any time.
START_MODE = "playing"

# --- Projector IR control (optional) ---
# When enabled, the same Home Assistant on/off command that starts and stops the
# show also powers the projector via ir-ctl. Configure in config.ini [projector].
IR_ENABLED = False

# --- Stage mode (--mode stage) ---
# The projectors become viewports onto ONE virtual stage wider than any window.
# This Pi shows the slice of the stage between STAGE_FROM and STAGE_TO, so a
# figure walking the stage crosses from window to window. From config.ini [stage].
STAGE_FROM = 0.0
STAGE_TO   = 0.4
FIG_SCALE  = 0.72       # figure height as a fraction of the window
STAGE_SPEED = 0.085     # stage units per second
STAGE_FREEZE_WHEN_WATCHED = False   # only move while nobody is detected
FIGURE_FACES = "right"  # which way the SOURCE frames face: "left" or "right"
# How far along the stage ONE full walk cycle carries the figure. Tie this to
# the art: if the feet slide forward (moonwalking) lower it; if the legs churn
# faster than the figure travels, raise it.
FIGURE_STRIDE = 0.075
FIGURE_ATLAS = ""       # path to a sprite atlas PNG (from sprite_atlas.py).
                        # Set it and stage mode uses the sprite shader instead
                        # of the procedural silhouette.

# Spirit trails are drawn through a HISTORY of past positions (not along the
# current velocity, which can only ever draw a straight line). Each renderer
# keeps its own history of the smoothed position it actually displayed, so both
# Pis draw matching trails without streaming any extra state.
SPIRIT_HIST_N = 8       # points per trail (7 segments)
SPIRIT_HIST_DT = 0.055  # seconds between samples — sets the trail's length

# --- Target tracking (brain only) ---
# When a target arrives on TARGET_TOPIC the eye snaps to it and follows; when no
# update arrives for TARGET_HOLD seconds it goes back to idle wandering.
TARGET_HOLD    = 2.0    # seconds to keep tracking after the last target update
ALERT_DILATION = 0.20   # pupil while locked on (lower = more constricted/focused)
# How the eye reacts the moment it notices someone. Weights need not sum to 1.
#   startle  — flares wide in surprise, then settles into the locked glare
#   anger    — snaps straight to a narrowed, hostile glare (no flare)
#   slow     — barely reacts at first, then slowly hardens into the glare
REACT_WEIGHTS  = {"startle": 0.5, "anger": 0.3, "slow": 0.2}
TRACK_SNAP     = 0.10   # seconds to whip to a newly acquired target
TRACK_FOLLOW   = 0.22   # follow responsiveness (0..1 per 60 fps frame)
# The eyes are usually projected ABOVE the people they're watching, so a target
# reported as level is really below them. This tilts the gaze down while
# tracking: 0 = none, 1 = fully down. Higher for eyes mounted higher / viewers
# standing closer. Per-Pi, from config.ini [control] track_look_down.
TRACK_LOOK_DOWN = 0.35

# --- Per-Pi placement (defaults; real values live in config.ini [display]) ---
EYE_CENTER_X = 0.50   # 0=left  .. 1=right   (fraction of screen)
EYE_CENTER_Y = 0.50   # 0=top   .. 1=bottom
EYE_SCALE    = 1.00   # overall size multiplier
MIRROR       = False  # flip this eye horizontally (glint side) so a two-eye
                      # pair is bilaterally symmetric. Set True on ONE projector.
MIRROR_GAZE  = False  # also flip gaze direction: the eyes diverge/converge like
                      # a cross-eyed pair. Leave False so both eyes track together.

# How far the gaze travels across the eye. 1.0 is the shader default; raise it
# so a tracked person is unmistakably being looked at (1.6-2.2 reads well on a
# house). Per-Pi, from config.ini [display] gaze_scale / gaze_scale_y.
GAZE_SCALE_X = 1.0
GAZE_SCALE_Y = 1.0

# --- Display ---
FULLSCREEN = True
FPS        = 60
# SDL video driver. "" = auto. Use "kmsdrm" to draw straight to HDMI from the
# console with no desktop (best for a boot-to-console decoration).
SDL_DRIVER = os.environ.get("SDL_VIDEODRIVER", "")

# --- Eye look ---
# All eyes are rendered by GLSL fragment shaders in eye_shaders.py; tune each
# eye's appearance (colors, pupil, veins, shading) in those shader strings.
# EYE_CENTER_X/Y and EYE_SCALE above position and size the eye on this Pi.

# --- Render smoothing (applied identically on both Pis => they stay matched) ---
LERP = {"gx": 0.5, "gy": 0.5, "blink": 0.6, "dilation": 0.2, "glow": 0.4,
        "open": 0.4, "slant": 0.4}

# ============================================================================

NEUTRAL = {"gx": 0.0, "gy": 0.0, "blink": 0.0, "dilation": 0.45, "glow": 1.0,
           "open": 1.0, "slant": 0.0}

_ON_WORDS = {"on", "1", "true", "play", "start", "yes", "open"}
_OFF_WORDS = {"off", "0", "false", "stop", "pause", "no", "close"}


def _parse_command(payload):
    """Map an MQTT command payload to True (play) / False (idle) / None (ignore).
    Accepts plain strings ('ON'/'off') or a JSON object with a 'state' field."""
    try:
        v = payload.decode(errors="ignore").strip().strip('"').lower()
    except Exception:
        return None
    if v.startswith("{"):
        try:
            v = str(json.loads(payload).get("state", "")).strip().lower()
        except Exception:
            return None
    if v in _ON_WORDS:
        return True
    if v in _OFF_WORDS:
        return False
    return None


def _parse_target(payload):
    """Parse a look-here target. Returns ('set', x, y, hold) with x,y in -1..1
    (x: -1 left .. +1 right, y: -1 down .. +1 up), or ('clear',), or None.
    Accepts {"x":..,"y":..[, "hold":..]}, a list of those (first is used),
    {"active": false}/{"state":"clear"}, or the bare word 'clear'."""
    try:
        raw = payload.decode(errors="ignore").strip()
    except Exception:
        return None
    if raw[:1] in "{[":
        try:
            d = json.loads(raw)
        except Exception:
            return None
        if isinstance(d, list):
            d = d[0] if d else None
        if not isinstance(d, dict):
            return None
        if d.get("active") is False or str(d.get("state", "")).lower() in ("clear", "lost", "off"):
            return ("clear",)
        if "x" in d and "y" in d:
            try:
                return ("set", float(d["x"]), float(d["y"]), float(d.get("hold", TARGET_HOLD)))
            except Exception:
                return None
        return None
    if raw.lower() in ("clear", "none", "lost", "off", ""):
        return ("clear",)
    return None



# ---------------------------------------------------------------------------
# The Director — brain-only. Produces the shared eye state each frame.
# ---------------------------------------------------------------------------
class Director:
    def __init__(self):
        self.s = dict(NEUTRAL)
        self.tx = self.ty = 0.0          # current gaze target (-1..1)
        self.fx = self.fy = 0.0          # eased gaze value
        self.dwell = 0.0                 # time to hold current target
        self.saccade = 0.0               # remaining tween time
        self.sacc_len = 0.12
        self.start = (0.0, 0.0)
        self.next_blink = random.uniform(2.5, 6.0)
        self.blink_t = -1.0              # >=0 while blinking
        self.blink_dur = 0.0
        self.double = False
        self.dil = 0.45
        self.dil_target = 0.45
        self.dil_timer = 2.0
        self.t = 0.0
        # target tracking (set from MQTT on the brain)
        self.track = None            # (x, y) external look-at target, or None
        self.track_deadline = 0.0    # wall-clock time the target expires
        self.tracking = False        # currently locked onto a target
        # mood / lid shape: eased open+slant streamed to both eyes
        self.open_v, self.slant_v = 1.0, 0.0
        self.open_t, self.slant_t = 1.0, 0.0
        self.mood = "neutral"
        self.react_style = None      # how it reacted to the current target
        self.mood_hold = 0.0         # time left in the current mood episode
        self.mood_timer = random.uniform(7.0, 15.0)   # until next idle episode

    def _set_mood(self, mood, hold):
        self.mood = mood
        self.mood_hold = hold
        self.open_t, self.slant_t = {
            "surprise": (1.35, -0.05),   # lids flare wide
            "anger":    (0.60,  0.55),   # heavy narrowed glare
            "glare":    (0.78,  0.40),   # locked-on stare while tracking
        }.get(mood, (1.0, 0.0))          # neutral

    def set_target(self, x, y, hold):
        # deadline first, so a reader that sees .track already sees a fresh deadline
        self.track_deadline = time.time() + max(0.1, hold)
        self.track = (max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)))

    def clear_target(self):
        self.track = None

    def _new_target(self, slow=False):
        self.start = (self.fx, self.fy)
        self.tx = random.uniform(-1, 1)
        self.ty = random.uniform(-1, 1)
        self.sacc_len = random.uniform(0.5, 0.9) if slow else random.uniform(0.09, 0.16)
        self.saccade = self.sacc_len
        self.dwell = random.uniform(0.7, 2.6)

    def update(self, dt):
        self.t += dt

        now = time.time()
        was_tracking = self.tracking
        if self.track is not None and now >= self.track_deadline:
            self.track = None                      # target expired -> idle
        tracking = self.track is not None

        # --- gaze ---
        if tracking:
            tx, ty = self.track
            # Tilt down: the eyes sit above the people they're watching, so a
            # target reported as level actually needs a downward look.
            ty = max(-1.0, min(1.0, ty - TRACK_LOOK_DOWN))
            if not self.tracking:                  # just acquired: whip to it
                self.tracking = True
                self.start = (self.fx, self.fy)
                self.tx, self.ty = tx, ty
                self.sacc_len = TRACK_SNAP
                self.saccade = self.sacc_len
            else:
                self.tx, self.ty = tx, ty          # follow the moving target
            if self.saccade > 0:
                self.saccade = max(0.0, self.saccade - dt)
                p = 1.0 - self.saccade / self.sacc_len
                e = 1.0 - (1.0 - p) ** 3
                self.fx = self.start[0] + (self.tx - self.start[0]) * e
                self.fy = self.start[1] + (self.ty - self.start[1]) * e
            else:
                k = 1.0 - (1.0 - TRACK_FOLLOW) ** (dt * FPS)
                self.fx += (self.tx - self.fx) * k
                self.fy += (self.ty - self.fy) * k
        else:
            if self.tracking:                      # just lost it: pause, then wander
                self.tracking = False
                self.saccade = 0.0
                self.start = (self.fx, self.fy)
                self.tx, self.ty = self.fx, self.fy
                self.dwell = random.uniform(0.3, 0.9)
            if self.saccade > 0:
                self.saccade = max(0.0, self.saccade - dt)
                p = 1.0 - self.saccade / self.sacc_len
                e = 1.0 - (1.0 - p) ** 3           # ease-out cubic
                self.fx = self.start[0] + (self.tx - self.start[0]) * e
                self.fy = self.start[1] + (self.ty - self.start[1]) * e
            else:
                self.dwell -= dt
                if self.dwell <= 0:
                    self._new_target(slow=random.random() < 0.15)
                    if random.random() < 0.25:      # sometimes blink on a look
                        self._start_blink()

        # --- blink scheduler (a lock-on stare doesn't start new blinks) ---
        if self.blink_t >= 0:
            self._advance_blink(dt)                 # always finish one in progress
        elif not tracking:
            self.next_blink -= dt
            if self.next_blink <= 0:
                self._start_blink(double=random.random() < 0.18)
                self.next_blink = random.uniform(2.5, 6.5)

        # --- mood: lid shape (open/slant), streamed like everything else ---
        if tracking and not was_tracking:
            # Vary how it reacts to being noticed, so it never feels canned.
            styles = list(REACT_WEIGHTS)
            style = random.choices(styles, weights=[REACT_WEIGHTS[s] for s in styles])[0]
            if style == "anger":
                self._set_mood("anger", random.uniform(1.2, 2.5))   # straight to hostile
            elif style == "slow":
                self._set_mood("neutral", random.uniform(0.6, 1.2))  # a beat, then hardens
            else:
                self._set_mood("surprise", random.uniform(0.35, 0.8))
            self.react_style = style
            self.mood_timer = random.uniform(7.0, 15.0)
        elif (not tracking) and was_tracking:
            self._set_mood("neutral", 0.0)         # lost it: relax
        elif tracking:
            self.mood_hold -= dt
            if self.mood != "glare" and self.mood_hold <= 0:
                # every reaction style resolves into the locked-on stare
                self._set_mood("glare", 1e9)
        else:
            if self.mood != "neutral":
                self.mood_hold -= dt
                if self.mood_hold <= 0:
                    self._set_mood("neutral", 0.0)
                    self.mood_timer = random.uniform(7.0, 15.0)
            else:
                self.mood_timer -= dt
                if self.mood_timer <= 0:           # idle mood episode
                    if random.random() < 0.65:
                        self._set_mood("anger", random.uniform(2.0, 4.5))
                    else:
                        self._set_mood("surprise", random.uniform(0.7, 1.2))
        # ease lids toward the mood (surprise snaps faster)
        rate = 10.0 if self.mood == "surprise" else 5.0
        k = min(1.0, dt * rate)
        self.open_v += (self.open_t - self.open_v) * k
        self.slant_v += (self.slant_t - self.slant_v) * k

        # --- pupil dilation ---
        # WIDEN_CHANCE drives how often the pupil blows open into the round
        # "black hole" (paired with ROUND_START up top). While locked on a
        # target the pupil holds an alert/focused size instead.
        WIDEN_CHANCE = 0.20
        if self.mood == "surprise":
            self.dil_target = 1.0                  # blown-wide round pupil
        elif self.mood == "anger":
            self.dil_target = 0.08                 # razor slit
        elif tracking:
            self.dil_target = ALERT_DILATION
        else:
            self.dil_timer -= dt
            if self.dil_timer <= 0:
                self.dil_timer = random.uniform(1.5, 4.0)
                roll = random.random()
                if roll < WIDEN_CHANCE:
                    self.dil_target = random.uniform(0.9, 1.0)    # sudden round widen
                elif roll < WIDEN_CHANCE + 0.15:
                    self.dil_target = random.uniform(0.05, 0.15)  # sharp constrict
                else:
                    self.dil_target = random.uniform(0.30, 0.60)
        self.dil += (self.dil_target - self.dil) * min(1.0, dt * 3.0)

        # --- glow: slow pulse + ember flicker ---
        pulse = 1.0 + 0.07 * math.sin(self.t * 1.3)
        flick = 1.0 + random.uniform(-0.05, 0.05)
        glow = pulse * flick * (1.0 - 0.85 * self.s["blink"])

        self.s.update(gx=self.fx, gy=self.fy, dilation=self.dil, glow=glow,
                      open=self.open_v, slant=self.slant_v)
        return self.s

    def _start_blink(self, double=False):
        self.blink_t = 0.0
        self.blink_dur = random.uniform(0.18, 0.24)
        self.double = double

    def _advance_blink(self, dt):
        self.blink_t += dt
        d = self.blink_dur
        p = self.blink_t / d
        if p >= 1.0:
            if self.double:
                self.double = False
                self.blink_t = 0.0
            else:
                self.blink_t = -1.0
                self.s["blink"] = 0.0
            return
        # close fast (first 40%), open a touch slower
        if p < 0.4:
            b = p / 0.4
        else:
            b = 1.0 - (p - 0.4) / 0.6
        self.s["blink"] = max(0.0, min(1.0, b))


# ---------------------------------------------------------------------------
# OpenGL helpers (PyOpenGL). Works on desktop core GL (Mac) and GLES (the Pi):
# the shader bodies are identical, only the #version header differs.
# ---------------------------------------------------------------------------
def _gl_source(body, stage, gles):
    body = re.sub(r"^\s*#version[^\n]*\n", "", body, count=1, flags=re.M).lstrip("\n")
    if gles:
        head = "#version 300 es\n" + ("precision highp float;\n" if stage == "frag" else "")
    else:
        head = "#version 330\n"
    return head + body


def _compile(body, stage_enum, stage, gles):
    sid = GL.glCreateShader(stage_enum)
    GL.glShaderSource(sid, _gl_source(body, stage, gles))
    GL.glCompileShader(sid)
    if GL.glGetShaderiv(sid, GL.GL_COMPILE_STATUS) != GL.GL_TRUE:
        raise RuntimeError(f"{stage} shader compile failed:\n"
                           + GL.glGetShaderInfoLog(sid).decode())
    return sid


def build_program(vert_body, frag_body, gles):
    vs = _compile(vert_body, GL.GL_VERTEX_SHADER, "vert", gles)
    fs = _compile(frag_body, GL.GL_FRAGMENT_SHADER, "frag", gles)
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vs)
    GL.glAttachShader(prog, fs)
    GL.glLinkProgram(prog)
    if GL.glGetProgramiv(prog, GL.GL_LINK_STATUS) != GL.GL_TRUE:
        raise RuntimeError("program link failed:\n" + GL.glGetProgramInfoLog(prog).decode())
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    return prog


def make_fullscreen_triangle(prog):
    vao = GL.glGenVertexArrays(1)
    GL.glBindVertexArray(vao)
    vbo = GL.glGenBuffers(1)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    verts = np.array([-1, -1, 3, -1, -1, 3], dtype="f4")
    GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
    loc = GL.glGetAttribLocation(prog, "in_pos")
    GL.glEnableVertexAttribArray(loc)
    GL.glVertexAttribPointer(loc, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
    return vao


def load_atlas(png_path):
    """Upload a sprite atlas PNG as a GL texture. Returns (tex, meta) or None."""
    try:
        from PIL import Image
    except ImportError:
        print("[stage] Pillow not installed — cannot load a sprite atlas", flush=True)
        return None
    meta_path = os.path.splitext(png_path)[0] + ".json"
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        img = Image.open(png_path).convert("RGBA")
    except Exception as e:
        print(f"[stage] could not load atlas {png_path}: {e}", flush=True)
        return None
    data = img.tobytes()
    tex = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
    GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, img.width, img.height, 0,
                    GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
    print(f"[stage] atlas {os.path.basename(png_path)}: {meta['frames']} frames "
          f"{meta['cols']}x{meta['rows']} cell {meta['cell_w']}x{meta['cell_h']}", flush=True)
    return tex, meta


_STAGE_UNIFORMS = ("iResolution", "iTime", "uLocal", "uFacing", "uPhase",
                   "uLit", "uCrouch", "uFigScale", "uMirror",
                   "uFrames", "uCols", "uRows", "uCellAspect", "uSrcFacing",
                   "uAtlas", "uDepth", "uYaw",
                   "uPos", "uLit", "uX", "uY", "uZ", "uVX", "uVY", "uVZ",
                   "uHX", "uHY", "uHZ")

_UNIFORMS = ("iResolution", "iTime", "uGaze", "uBlink", "uDilation",
             "uGlow", "uCenter", "uEyeR", "uMirror", "uOpen", "uSlant",
             "uGazeScale")


# ---------------------------------------------------------------------------
# The Eye application
# ---------------------------------------------------------------------------
class EyeApp:
    def __init__(self, role, windowed, solo, eye="demon", show_stats=False,
                 mode="eyes"):
        self.role = role
        self.solo = solo
        self.running = True
        self.mode = mode
        # Each mode has its own streamed state schema and easing rates.
        if mode == "stage":
            if stage_mod is None:
                raise SystemExit("[fatal] stage mode needs stage.py and stage_shaders.py")
            if eye == "mansion":
                self.schema = stage_mod.BALLROOM_NEUTRAL
                self.lerp = stage_mod.BALLROOM_LERP
            elif eye == "spirits":
                self.schema = stage_mod.SPIRIT_NEUTRAL
                self.lerp = stage_mod.SPIRIT_LERP
            else:
                self.schema = stage_mod.STAGE_NEUTRAL
                self.lerp = stage_mod.STAGE_LERP
        else:
            self.schema = NEUTRAL
            self.lerp = LERP
        self.net = dict(self.schema)      # latest authoritative state
        self.disp = dict(self.schema)     # smoothed on-screen state
        self.t = 0.0
        # playback gate: solo has no controller so always plays; otherwise start
        # per START_MODE and let MQTT commands toggle it.
        self.active = True if solo else (START_MODE == "playing")

        if SDL_DRIVER:
            os.environ["SDL_VIDEODRIVER"] = SDL_DRIVER
        pygame.init()
        # Init video explicitly so a failure here is reported clearly rather
        # than surfacing later as "video system not initialized". Under a
        # service (no desktop/seat) this is where KMSDRM problems show up.
        try:
            pygame.display.init()
        except pygame.error as e:
            drv = os.environ.get("SDL_VIDEODRIVER", "(auto)")
            raise SystemExit(
                f"[fatal] could not initialise video (SDL_VIDEODRIVER={drv}): {e}\n"
                "        If running as a systemd service on the console, the user "
                "needs the display: check it is in the 'video' and 'render' groups, "
                "that the Pi boots to console (no desktop holding the display), and "
                "that /dev/dri exists."
            )

        # Request a context the platform can actually give: GLES 3.0 on the Pi
        # (its GPU tops out at desktop GL 3.1 but supports GLES 3.1), desktop
        # core 3.3 on macOS (which has no GLES). EYE_GL=es|core forces it.
        force = os.environ.get("EYE_GL", "").lower()
        self.gles = (force == "es") or (force != "core" and sys.platform != "darwin")
        if self.gles:
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK,
                                            pygame.GL_CONTEXT_PROFILE_ES)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)
        else:
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK,
                                            pygame.GL_CONTEXT_PROFILE_CORE)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        glflags = pygame.OPENGL | pygame.DOUBLEBUF
        if windowed:
            pygame.display.set_mode((1280, 720), glflags)
        else:
            pygame.display.set_mode((0, 0), glflags | (pygame.FULLSCREEN if FULLSCREEN else 0))
        pygame.display.set_caption(f"{eye.capitalize()} Eye")
        try:
            pygame.mouse.set_visible(False)   # only valid once a mode is set
        except pygame.error:
            pass                               # harmless if the driver has no cursor
        self.W, self.H = pygame.display.get_surface().get_size()
        print(f"[gl] sdl_driver={pygame.display.get_driver()} "
              f"pyopengl={os.environ.get('PYOPENGL_PLATFORM', 'default')}", flush=True)
        print(f"[gl] {GL.glGetString(GL.GL_VERSION).decode()} "
              f"({'GLES' if self.gles else 'core'})", flush=True)

        # one fullscreen triangle drawn by the selected shader
        if self.mode == "stage":
            # An explicit --eye wins; otherwise a configured atlas selects the
            # sprite shader, falling back to the procedural silhouette.
            if eye in STAGE_SHADERS:
                want = eye
            elif FIGURE_ATLAS:
                want = "sprite"
            else:
                want = "silhouette"
            vert, frag = STAGE_VERT, STAGE_SHADERS[want]
            names = _STAGE_UNIFORMS
            self.stage_shader = want
        else:
            vert, frag = VERT, EYE_SHADERS[eye]
            names = _UNIFORMS
        self.prog = build_program(vert, frag, self.gles)
        self.vao = make_fullscreen_triangle(self.prog)
        GL.glUseProgram(self.prog)
        self._uloc = {n: GL.glGetUniformLocation(self.prog, n) for n in names}

        self._hist = None
        self._hist_t = 0.0
        if self.mode == "stage" and getattr(self, "stage_shader", "") == "spirits":
            n = stage_mod.SPIRIT_SLOTS
            self._hist = [[(0.5, 0.5, 0.5)] * SPIRIT_HIST_N for _ in range(n)]

        self.atlas = None
        if self.mode == "stage" and getattr(self, "stage_shader", "") == "sprite":
            self.atlas = load_atlas(FIGURE_ATLAS)
            if self.atlas is None:
                raise SystemExit(f"[fatal] figure_atlas set but could not be loaded: "
                                 f"{FIGURE_ATLAS}")

        if role != "brain":
            self.director = None
        elif self.mode == "stage" and getattr(self, "stage_shader", "") == "spirits":
            self.director = stage_mod.SpiritDirector(
                speed=STAGE_SPEED, freeze_when_watched=STAGE_FREEZE_WHEN_WATCHED)
        elif self.mode == "stage" and getattr(self, "stage_shader", "") == "mansion":
            self.director = stage_mod.BallroomDirector(
                speed=STAGE_SPEED, freeze_when_watched=STAGE_FREEZE_WHEN_WATCHED)
        elif self.mode == "stage" and getattr(self, "stage_shader", "") == "ghost":
            self.director = stage_mod.DriftDirector(
                speed=STAGE_SPEED, freeze_when_watched=STAGE_FREEZE_WHEN_WATCHED)
        elif self.mode == "stage":
            self.director = stage_mod.StageDirector(
                speed=STAGE_SPEED, stride=FIGURE_STRIDE,
                pause_windows=(STAGE_FROM + (STAGE_TO - STAGE_FROM) * 0.5,),
                freeze_when_watched=STAGE_FREEZE_WHEN_WATCHED)
        else:
            self.director = Director()
        self.client = None
        if not solo and mqtt is not None:
            self._setup_mqtt()

        self._pub_interval = 1.0 / PUBLISH_HZ
        self._last_pub = 0.0

        signal.signal(signal.SIGTERM, lambda *_: self.stop())

        self.stats = None
        if show_stats and stats_mod:
            self.stats = stats_mod.Stats()
            print(self.stats.describe(), flush=True)
        elif show_stats:
            print("[stats] stats.py not found", flush=True)

        if self.active:
            self._set_projector(True)   # booting straight into the show

    def _set_projector(self, on):
        """Power the projector to match playback. Runs on a worker thread so the
        IR send (and any warm-up wait) never stalls the render loop."""
        if not (IR_ENABLED and ir_control):
            return
        import threading
        fn = ir_control.power_on if on else ir_control.power_off
        threading.Thread(target=fn, daemon=True).start()

    def _u1(self, name, x):
        loc = self._uloc.get(name, -1)
        if loc != -1:
            GL.glUniform1f(loc, x)

    def _u2(self, name, x, y):
        loc = self._uloc.get(name, -1)
        if loc != -1:
            GL.glUniform2f(loc, x, y)

    # -- MQTT --
    def _setup_mqtt(self):
        cid = f"demoneye-{self.role}-{random.randint(0,9999)}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            print(f"[mqtt] connect failed: {e} — running solo", file=sys.stderr)
            self.client = None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[mqtt] connected rc={reason_code}")
        client.subscribe(COMMAND_TOPIC, qos=1)   # both roles listen for on/off
        if self.role == "brain":
            client.subscribe(TARGET_TOPIC, qos=0)  # only the brain steers gaze
        if self.role == "follower":
            client.subscribe(TOPIC, qos=0)

    def _on_message(self, client, userdata, msg):
        if msg.topic == COMMAND_TOPIC:
            cmd = _parse_command(msg.payload)
            if cmd is not None and cmd != self.active:
                self.active = cmd
                print(f"[cmd] {'playing' if cmd else 'idle (black)'}", flush=True)
                self._set_projector(cmd)
            return
        if msg.topic == TARGET_TOPIC:
            if self.director is not None:
                t = _parse_target(msg.payload)
                if self.mode == "stage":
                    # stage figures don't aim, they react to being watched
                    self.director.set_watched(bool(t and t[0] == "set"))
                elif t and t[0] == "set":
                    self.director.set_target(t[1], t[2], t[3])
                elif t and t[0] == "clear":
                    self.director.clear_target()
            return
        try:
            d = json.loads(msg.payload)
            self.net = {k: float(d.get(k, self.schema[k])) for k in self.schema}  # atomic
        except Exception:
            pass

    def _publish(self, state):
        if not self.client:
            return
        payload = json.dumps({k: round(state[k], 4) for k in self.schema})
        self.client.publish(TOPIC, payload, qos=0)

    # -- main loop --
    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            dt = clock.tick(FPS) / 1000.0
            dt = min(dt, 0.05)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.stop()
                elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.stop()

            if self.role == "brain":
                self.net = dict(self.director.update(dt))
                now = time.time()
                if now - self._last_pub >= self._pub_interval:
                    self._publish(self.net)
                    self._last_pub = now

            self.t += dt
            self._smooth(dt)
            self._record_history(dt)
            self._draw()
            pygame.display.flip()

            if self.stats:
                line = self.stats.frame()
                if line:
                    extra = ""
                    if self.director is not None:
                        if self.mode == "stage" and "pos" not in self.disp:
                            vis = sum(1 for k, v in self.disp.items()
                                      if k.startswith("lit") and v > 0.05)
                            label = ("spirits" if getattr(self, "stage_shader", "") == "spirits"
                                     else "dancers")
                            extra = f"  [{vis} {label}]"
                        elif self.mode == "stage":
                            extra = f"  [pos {self.disp['pos']:.2f}]"
                        else:
                            extra = f"  [{self.director.mood}]"
                    print(f"\r{line}{extra}   ", end="", flush=True)

        if self.stats:
            print()     # end the refreshing stats line cleanly
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        pygame.quit()

    def _smooth(self, dt):
        # frame-rate-independent lerp toward the authoritative state
        for k in self.schema:
            k_lerp = 1.0 - (1.0 - self.lerp.get(k, 0.4)) ** (dt * FPS)
            self.disp[k] += (self.net[k] - self.disp[k]) * k_lerp

    def _record_history(self, dt):
        """Sample each spirit's displayed position at a fixed interval so the
        trail has a consistent length whatever the frame rate. A teleport
        (blink) resets the trail instead of smearing a line across the room."""
        if self._hist is None:
            return
        self._hist_t += dt
        if self._hist_t < SPIRIT_HIST_DT:
            return
        self._hist_t = 0.0
        s = self.disp
        for i, h in enumerate(self._hist):
            p = (s[f"x{i}"], s[f"y{i}"], s[f"z{i}"])
            last = h[-1]
            jump = ((p[0]-last[0])**2 + (p[1]-last[1])**2 + (p[2]-last[2])**2) ** 0.5
            if jump > 0.22 or s[f"lit{i}"] < 0.02:
                self._hist[i] = [p] * SPIRIT_HIST_N      # blinked / faded: no smear
            else:
                h.append(p)
                del h[0]

    def _draw(self):
        s = self.disp
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if not self.active:          # idle: projector shows pure black
            return
        GL.glUseProgram(self.prog)

        if self.mode == "stage" and getattr(self, "stage_shader", "") == "spirits":
            span = max(1e-6, STAGE_TO - STAGE_FROM)
            n = stage_mod.SPIRIT_SLOTS
            self._u2("iResolution", float(self.W), float(self.H))
            self._u1("iTime", float(self.t))
            self._u1("uFigScale", float(FIG_SCALE))
            self._u1("uMirror", 1.0 if MIRROR else 0.0)
            for name, vals in (
                ("uX",   [(s[f"x{i}"] - STAGE_FROM) / span for i in range(n)]),
                ("uY",   [s[f"y{i}"] for i in range(n)]),
                ("uZ",   [s[f"z{i}"] for i in range(n)]),
                ("uLit", [max(0.0, min(1.0, s[f"lit{i}"])) for i in range(n)]),
            ):
                loc = self._uloc.get(name, -1)
                if loc != -1:
                    GL.glUniform1fv(loc, n, (GL.GLfloat * n)(*[float(v) for v in vals]))
            # the trail: every spirit's past positions, flattened [spirit][point]
            hn = n * SPIRIT_HIST_N
            hx, hy, hz = [], [], []
            for i in range(n):
                for (px, py, pz) in self._hist[i]:
                    hx.append((px - STAGE_FROM) / span); hy.append(py); hz.append(pz)
            for name, vals in (("uHX", hx), ("uHY", hy), ("uHZ", hz)):
                loc = self._uloc.get(name, -1)
                if loc != -1:
                    GL.glUniform1fv(loc, hn, (GL.GLfloat * hn)(*[float(v) for v in vals]))
            GL.glBindVertexArray(self.vao)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            return

        if self.mode == "stage" and getattr(self, "stage_shader", "") == "mansion":
            span = max(1e-6, STAGE_TO - STAGE_FROM)
            n = stage_mod.BALLROOM_SLOTS
            order = sorted(range(n), key=lambda i: -s[f"depth{i}"])   # far first
            self._u2("iResolution", float(self.W), float(self.H))
            self._u1("iTime", float(self.t))
            self._u1("uFigScale", float(FIG_SCALE))
            self._u1("uMirror", 1.0 if MIRROR else 0.0)
            for name, vals in (
                ("uPos",   [(s[f"pos{i}"] - STAGE_FROM) / span for i in order]),
                ("uDepth", [s[f"depth{i}"] for i in order]),
                ("uYaw",   [math.atan2(s[f"yawy{i}"], s[f"yawx{i}"]) for i in order]),
                ("uPhase", [s[f"phase{i}"] for i in order]),
                ("uLit",   [max(0.0, min(1.0, s[f"lit{i}"])) for i in order]),
            ):
                loc = self._uloc.get(name, -1)
                if loc != -1:
                    GL.glUniform1fv(loc, n, (GL.GLfloat * n)(*[float(v) for v in vals]))
            GL.glBindVertexArray(self.vao)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            return

        if self.mode == "stage":
            # Map the figure's position on the whole stage into THIS window.
            # <0 or >1 means it's beyond our edges (behind the wall) and the
            # shader simply draws nothing but the lit pane.
            span = max(1e-6, STAGE_TO - STAGE_FROM)
            local = (s["pos"] - STAGE_FROM) / span
            self._u2("iResolution", float(self.W), float(self.H))
            self._u1("iTime", float(self.t))
            self._u1("uLocal", float(local))
            self._u1("uFacing", 1.0 if s["facing"] >= 0 else -1.0)
            self._u1("uPhase", float(s["phase"]))
            self._u1("uLit", float(s["lit"]))
            self._u1("uCrouch", float(s["crouch"]))
            self._u1("uFigScale", float(FIG_SCALE))
            self._u1("uMirror", 1.0 if MIRROR else 0.0)
            self._u1("uDepth", float(s.get("depth", 0.0)))
            self._u1("uYaw", float(math.atan2(s.get("yawy", 0.0), s.get("yawx", 1.0))))
            if self.atlas:
                tex, meta = self.atlas
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                loc = self._uloc.get("uAtlas", -1)
                if loc != -1:
                    GL.glUniform1i(loc, 0)
                self._u1("uFrames", float(meta["frames"]))
                self._u1("uCols", float(meta["cols"]))
                self._u1("uRows", float(meta["rows"]))
                self._u1("uCellAspect", float(meta["cell_w"]) / max(1.0, float(meta["cell_h"])))
                self._u1("uSrcFacing", -1.0 if FIGURE_FACES.startswith("l") else 1.0)
            GL.glBindVertexArray(self.vao)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            return
        # MIRROR_GAZE flips look direction; MIRROR flips glint/lighting side.
        gx = -s["gx"] if MIRROR_GAZE else s["gx"]
        self._u2("iResolution", float(self.W), float(self.H))
        self._u1("iTime", float(self.t))
        self._u2("uGaze", float(gx), float(s["gy"]))
        self._u1("uBlink", float(max(0.0, min(1.0, s["blink"]))))
        self._u1("uDilation", float(max(0.0, min(1.0, s["dilation"]))))
        self._u1("uGlow", float(s["glow"]))
        # uCenter is fraction of screen, y measured up (GL); config y is top-down
        self._u2("uCenter", float(EYE_CENTER_X), float(1.0 - EYE_CENTER_Y))
        self._u1("uEyeR", float(self.H * 0.42 * EYE_SCALE))
        self._u1("uMirror", 1.0 if MIRROR else 0.0)
        self._u1("uOpen", float(max(0.0, min(1.6, s["open"]))))
        self._u1("uSlant", float(max(-1.0, min(1.0, s["slant"]))))
        self._u2("uGazeScale", float(GAZE_SCALE_X), float(GAZE_SCALE_Y))
        GL.glBindVertexArray(self.vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

    def stop(self):
        self.running = False


def load_config(path):
    """Load per-machine settings from config.ini, overriding module defaults.
    Missing file or missing keys fall back to the defaults defined above.
    Precedence: CLI --host  >  env MQTT_USER/MQTT_PASS  >  config.ini  >  defaults.
    """
    global MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, TOPIC, COMMAND_TOPIC, TARGET_TOPIC
    global EYE_CENTER_X, EYE_CENTER_Y, EYE_SCALE, MIRROR, MIRROR_GAZE, START_MODE, TARGET_HOLD
    global GAZE_SCALE_X, GAZE_SCALE_Y, IR_ENABLED, TRACK_LOOK_DOWN
    global STAGE_FROM, STAGE_TO, FIG_SCALE, STAGE_SPEED, STAGE_FREEZE_WHEN_WATCHED
    global FIGURE_ATLAS, FIGURE_FACES, FIGURE_STRIDE

    cp = configparser.ConfigParser()
    # seed with current module defaults so there's a single source of truth
    cp.read_dict({
        "mqtt": {
            "host": MQTT_HOST, "port": str(MQTT_PORT),
            "username": MQTT_USER, "password": MQTT_PASS, "topic": TOPIC,
            "command_topic": COMMAND_TOPIC, "target_topic": TARGET_TOPIC,
        },
        "display": {
            "center_x": str(EYE_CENTER_X), "center_y": str(EYE_CENTER_Y),
            "scale": str(EYE_SCALE),
            "mirror": str(MIRROR), "mirror_gaze": str(MIRROR_GAZE),
            "gaze_scale": str(GAZE_SCALE_X), "gaze_scale_y": str(GAZE_SCALE_Y),
        },
        "stage": {"stage_from": str(STAGE_FROM), "stage_to": str(STAGE_TO),
                  "figure_scale": str(FIG_SCALE), "speed": str(STAGE_SPEED),
                  "freeze_when_watched": str(STAGE_FREEZE_WHEN_WATCHED),
                  "figure_atlas": FIGURE_ATLAS, "figure_faces": FIGURE_FACES,
                  "figure_stride": str(FIGURE_STRIDE)},
        "control": {"start": START_MODE, "target_hold": str(TARGET_HOLD),
                    "track_look_down": str(TRACK_LOOK_DOWN)},
        "projector": {"ir_enabled": str(IR_ENABLED), "keymap": "/home/fpp/groview.toml",
                      "device": "", "power_key": "KEY_POWER",
                      "separate_on_off": "false", "on_key": "KEY_POWER",
                      "off_key": "KEY_POWER", "repeat": "1",
                      "off_confirm": "0", "off_confirm_gap_s": "2.0",
                      "warmup_s": "0"},
    })
    if path and os.path.exists(path):
        cp.read(path)
        print(f"[config] loaded {path}")
    else:
        print(f"[config] {path} not found — using built-in defaults")

    MQTT_HOST = cp.get("mqtt", "host")
    MQTT_PORT = cp.getint("mqtt", "port")
    MQTT_USER = os.environ.get("MQTT_USER") or cp.get("mqtt", "username")
    MQTT_PASS = os.environ.get("MQTT_PASS") or cp.get("mqtt", "password")
    TOPIC     = cp.get("mqtt", "topic")
    COMMAND_TOPIC = cp.get("mqtt", "command_topic")
    TARGET_TOPIC = cp.get("mqtt", "target_topic")
    START_MODE = cp.get("control", "start").strip().lower()
    if START_MODE not in ("playing", "idle"):
        START_MODE = "playing"
    TARGET_HOLD = cp.getfloat("control", "target_hold")
    TRACK_LOOK_DOWN = cp.getfloat("control", "track_look_down")
    STAGE_FROM = cp.getfloat("stage", "stage_from")
    STAGE_TO = cp.getfloat("stage", "stage_to")
    FIG_SCALE = cp.getfloat("stage", "figure_scale")
    STAGE_SPEED = cp.getfloat("stage", "speed")
    STAGE_FREEZE_WHEN_WATCHED = cp.getboolean("stage", "freeze_when_watched")
    FIGURE_FACES = cp.get("stage", "figure_faces").strip().lower()
    FIGURE_STRIDE = cp.getfloat("stage", "figure_stride")
    FIGURE_ATLAS = cp.get("stage", "figure_atlas").strip()
    if FIGURE_ATLAS and not os.path.isabs(FIGURE_ATLAS):
        FIGURE_ATLAS = os.path.join(os.path.dirname(os.path.abspath(path or ".")), FIGURE_ATLAS)
    IR_ENABLED = cp.getboolean("projector", "ir_enabled")
    if IR_ENABLED and ir_control:
        ir_control.KEYMAP = cp.get("projector", "keymap")
        ir_control.DEVICE = cp.get("projector", "device")
        ir_control.POWER_KEY = cp.get("projector", "power_key")
        ir_control.SEPARATE_ON_OFF = cp.getboolean("projector", "separate_on_off")
        ir_control.ON_KEY = cp.get("projector", "on_key")
        ir_control.OFF_KEY = cp.get("projector", "off_key")
        ir_control.REPEAT = cp.getint("projector", "repeat")
        ir_control.OFF_CONFIRM = cp.getint("projector", "off_confirm")
        ir_control.OFF_CONFIRM_GAP = cp.getfloat("projector", "off_confirm_gap_s")
        ir_control.WARMUP = cp.getfloat("projector", "warmup_s")
        print(f"[ir] projector control enabled (ir-ctl "
              f"{'found' if ir_control.available() else 'NOT FOUND'})")
    EYE_CENTER_X = cp.getfloat("display", "center_x")
    EYE_CENTER_Y = cp.getfloat("display", "center_y")
    EYE_SCALE    = cp.getfloat("display", "scale")
    MIRROR       = cp.getboolean("display", "mirror")
    MIRROR_GAZE  = cp.getboolean("display", "mirror_gaze")
    GAZE_SCALE_X = cp.getfloat("display", "gaze_scale")
    GAZE_SCALE_Y = cp.getfloat("display", "gaze_scale_y")


def main():
    default_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    ap = argparse.ArgumentParser(description="Halloween eye projection")
    ap.add_argument("--role", choices=["brain", "follower"], required=True)
    ap.add_argument("--mode", choices=["eyes", "stage"], default="eyes",
                    help="eyes = an eye per projector; stage = projectors are "
                         "viewports onto one wide stage a figure walks across")
    ap.add_argument("--eye", default="demon",
                    help="eyes mode: demon|human|walker.  stage mode: silhouette")
    ap.add_argument("--windowed", action="store_true", help="run in a window (testing)")
    ap.add_argument("--solo", action="store_true", help="no MQTT (single-machine test)")
    ap.add_argument("--config", default=default_cfg, help="path to config.ini")
    ap.add_argument("--host", help="override MQTT host from config")
    ap.add_argument("--start", choices=["playing", "idle"],
                    help="override start mode: render now, or wait for an on command")
    ap.add_argument("--stats", action="store_true",
                    help="print FPS / CPU / GPU / temperature while running")
    args = ap.parse_args()

    load_config(args.config)
    global MQTT_HOST, START_MODE
    if args.host:
        MQTT_HOST = args.host
    if args.start:
        START_MODE = args.start

    if not args.solo and mqtt is None:
        print("paho-mqtt not installed; use --solo to test without it.", file=sys.stderr)
        sys.exit(1)

    if args.mode == "eyes" and args.eye not in EYE_SHADERS:
        print(f"--eye must be one of {sorted(EYE_SHADERS)} in eyes mode", file=sys.stderr)
        sys.exit(1)
    EyeApp(args.role, args.windowed, args.solo, eye=args.eye,
           show_stats=args.stats, mode=args.mode).run()


if __name__ == "__main__":
    main()
