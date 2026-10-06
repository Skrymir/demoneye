"""
The eye Director (brain only): produces the streamed eye state each frame —
gaze saccades, blinks, pupil, glow, moods and target tracking. Also the two
MQTT payload parsers it shares with the app.
"""

import json
import math
import random
import time

import settings as S

_ON_WORDS = {"on", "1", "true", "play", "start", "yes", "open"}
_OFF_WORDS = {"off", "0", "false", "stop", "pause", "no", "close"}

# Streamed eye state and the per-key easing the renderer applies (identical on
# both Pis, so they stay matched).
NEUTRAL = {"gx": 0.0, "gy": 0.0, "blink": 0.0, "dilation": 0.45, "glow": 1.0,
           "open": 1.0, "slant": 0.0}
LERP = {"gx": 0.5, "gy": 0.5, "blink": 0.6, "dilation": 0.2, "glow": 0.4,
        "open": 0.4, "slant": 0.4}

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
                return ("set", float(d["x"]), float(d["y"]), float(d.get("hold", S.TARGET_HOLD)))
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
            ty = max(-1.0, min(1.0, ty - S.TRACK_LOOK_DOWN))
            if not self.tracking:                  # just acquired: whip to it
                self.tracking = True
                self.start = (self.fx, self.fy)
                self.tx, self.ty = tx, ty
                self.sacc_len = S.TRACK_SNAP
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
                k = 1.0 - (1.0 - S.TRACK_FOLLOW) ** (dt * S.FPS)
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
            styles = list(S.REACT_WEIGHTS)
            style = random.choices(styles, weights=[S.REACT_WEIGHTS[s] for s in styles])[0]
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
            self.dil_target = S.ALERT_DILATION
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
