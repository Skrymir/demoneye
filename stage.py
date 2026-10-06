#!/usr/bin/env python3
"""
Stage mode — one virtual stage spanning several windows.

Instead of each projector showing its own eye, the two (or more) projectors are
VIEWPORTS onto a single continuous stage that is wider than any one window. The
brain streams one figure position along that stage; each Pi renders whatever
falls inside its own span and black otherwise.

    stage:   0.0 ......................................................... 1.0
    window1: [0.00 -- 0.40]              window2:          [0.60 -- 1.00]
                          \\____ solid wall ____/

A figure walking the stage appears in window 1, leaves its right edge, crosses
the gap of real wall, then enters window 2 — so it reads as something moving
THROUGH the house. The gap is the effect: vanishing at one window and
reappearing at the next sells "it's behind the wall" far better than anything
that happens inside a single frame.

Set each Pi's span with stage_from / stage_to in config.ini [stage].
"""

import math
import random
import time

# Streamed state for stage mode (mirrors NEUTRAL/LERP for the eyes).
#   pos    0..1 position along the whole stage
#   facing -1 left, +1 right
#   phase  walk-cycle phase 0..1 (streamed so both windows step in time)
#   lit    window glow level 0..1
#   depth  0 at the window .. 1 far back in the room (3D figures)
#   yawx/yawy  cos/sin of the facing angle (0 = facing the viewer). Streamed as
#          a vector so the follower can lerp it without spinning at the +-180 wrap.
STAGE_NEUTRAL = {"pos": 0.5, "facing": 1.0, "phase": 0.0, "lit": 1.0,
                 "depth": 0.0, "yawx": 1.0, "yawy": 0.0}

# Position and phase must NOT be eased much or the figure lags and the legs
# desync; the brain already produces smooth motion.
STAGE_LERP = {"pos": 0.9, "facing": 0.25, "phase": 0.95, "lit": 0.25,
              "depth": 0.6, "yawx": 0.5, "yawy": 0.5}


class StageDirector:
    """Brain-only. Walks a figure back and forth across the virtual stage,
    pausing at windows, occasionally lingering. If `freeze_when_watched` is on,
    it stops moving whenever a target is present — so it only ever advances
    while nobody is looking, which people find deeply unsettling."""

    def __init__(self, speed=0.085, pause_windows=(), freeze_when_watched=False,
                 stride=0.075):
        self.s = dict(STAGE_NEUTRAL)
        self.pos = 0.5
        self.facing = 1.0
        self.phase = 0.0
        self.lit = 1.0
        self.speed = speed              # stage units per second
        # Stage distance covered by ONE full walk cycle. Advancing the phase by
        # distance (not by time) keeps the feet planted at any speed — change
        # the walking speed and the legs automatically keep up.
        self.stride = max(1e-4, stride)
        self.pause_windows = list(pause_windows)
        self.freeze_when_watched = freeze_when_watched
        self.paused = 0.0               # seconds left of a pause
        self.next_pause_at = None
        self.t = 0.0
        self.watched = False            # set from tracking
        self._pick_next_pause()

    def _pick_next_pause(self):
        if self.pause_windows and random.random() < 0.7:
            self.next_pause_at = random.choice(self.pause_windows)
        else:
            self.next_pause_at = random.uniform(0.05, 0.95)

    def set_watched(self, watched):
        self.watched = watched

    def update(self, dt):
        self.t += dt

        moving = True
        if self.freeze_when_watched and self.watched:
            moving = False              # someone is looking: hold perfectly still
        elif self.paused > 0:
            self.paused = max(0.0, self.paused - dt)
            moving = False

        if moving:
            prev = self.pos
            self.pos += self.facing * self.speed * dt
            # turn around at the ends, with a beat before setting off again
            if self.pos >= 1.0:
                self.pos = 1.0
                self.facing = -1.0
                self.paused = random.uniform(1.5, 4.0)
                self._pick_next_pause()
            elif self.pos <= 0.0:
                self.pos = 0.0
                self.facing = 1.0
                self.paused = random.uniform(1.5, 4.0)
                self._pick_next_pause()
            # pause when we cross the chosen spot (often a window centre)
            elif self.next_pause_at is not None and \
                    (prev - self.next_pause_at) * (self.pos - self.next_pause_at) <= 0:
                self.paused = random.uniform(2.0, 6.0)
                self._pick_next_pause()
            # advance the walk cycle by DISTANCE so the feet never slide
            self.phase = (self.phase + abs(self.pos - prev) / self.stride) % 1.0
        else:
            # settle the legs to a neutral stance when stopped
            tgt = 0.0
            self.phase += (tgt - self.phase) * min(1.0, dt * 4.0)

        # slow breathing-ish posture drift, and a lamp-like flicker in the glow
        self.lit = 1.0 + 0.05 * math.sin(self.t * 1.9) + random.uniform(-0.02, 0.02)

        self.s.update(pos=self.pos, facing=self.facing, phase=self.phase,
                      lit=self.lit)
        return self.s


class FollowDirector(StageDirector):
    """Variant that tracks a real person instead of wandering: the figure keeps
    pace with a tracked target so it shadows people along the house."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.target_pos = None
        self.deadline = 0.0

    def set_target_stage_pos(self, p, hold=2.0):
        self.target_pos = max(0.0, min(1.0, p))
        self.deadline = time.time() + hold

    def update(self, dt):
        if self.target_pos is not None and time.time() >= self.deadline:
            self.target_pos = None
        if self.target_pos is None:
            return super().update(dt)

        self.t += dt
        d = self.target_pos - self.pos
        if abs(d) > 0.01:
            step = math.copysign(min(abs(d), self.speed * 1.6 * dt), d)
            self.pos += step
            self.facing = 1.0 if step > 0 else -1.0
            self.phase = (self.phase + abs(step) / self.stride) % 1.0
        self.lit = 1.0 + 0.05 * math.sin(self.t * 1.9)
        self.s.update(pos=self.pos, facing=self.facing, phase=self.phase,
                      lit=self.lit)
        return self.s


def _turn_toward(cur, tgt, rate):
    """Ease an angle toward a target along the shortest arc."""
    d = (tgt - cur + math.pi) % (2*math.pi) - math.pi
    return cur + d * rate


class DriftDirector:
    """Brain-only. A ghost that wanders a 3D room behind the windows: it drifts
    between waypoints in x (along the stage) and depth (toward/away from the
    glass), turns to face the way it's moving, pauses, and sometimes turns to
    stare out of the window. If freeze_when_watched is on it stops and TURNS TO
    FACE the watcher — it noticed you."""

    def __init__(self, speed=0.05, freeze_when_watched=False, stare_chance=0.5):
        self.s = dict(STAGE_NEUTRAL)
        self.pos = 0.5
        self.depth = 0.5
        self.yaw = 0.0                  # 0 = facing the viewer
        self.phase = 0.0
        self.lit = 1.0
        self.speed = speed              # stage units per second
        self.freeze_when_watched = freeze_when_watched
        self.stare_chance = stare_chance
        self.watched = False
        self.t = 0.0
        self.tx, self.tz = self.pos, self.depth
        self.paused = 0.0
        self.staring = 0.0              # seconds left of a deliberate stare
        self._new_waypoint()

    def set_watched(self, watched):
        self.watched = watched

    def _new_waypoint(self):
        # wander anywhere in the room; sometimes drift right up to the glass
        self.tx = random.uniform(-0.08, 1.08)
        self.tz = random.choice([random.uniform(0.0, 0.15),
                                 random.uniform(0.2, 0.9),
                                 random.uniform(0.2, 0.9)])

    def update(self, dt):
        self.t += dt
        yaw_rate = min(1.0, dt * 2.2)

        if self.freeze_when_watched and self.watched:
            # noticed: hold position and slowly turn to look straight at them
            self.yaw = _turn_toward(self.yaw, 0.0, yaw_rate * 0.6)
        elif self.staring > 0:
            self.staring -= dt
            self.yaw = _turn_toward(self.yaw, 0.0, yaw_rate)
            self.depth += (max(0.0, self.depth - 0.15) - self.depth) * min(1.0, dt*0.4)
        elif self.paused > 0:
            self.paused -= dt
        else:
            dx, dz = self.tx - self.pos, self.tz - self.depth
            dist = math.hypot(dx, dz)
            if dist < 0.02:
                self.paused = random.uniform(1.0, 4.0)
                if random.random() < self.stare_chance:
                    self.staring = random.uniform(2.5, 6.0)
                self._new_waypoint()
            else:
                # ease in/out: slower near the waypoint
                spd = self.speed * (0.35 + 0.65*min(1.0, dist/0.25))
                step = min(dist, spd * dt)
                self.pos += dx/dist * step
                self.depth += dz/dist * step
                # face the direction of travel (0 = toward the viewer)
                self.yaw = _turn_toward(self.yaw, math.atan2(dx, -dz), yaw_rate)

        self.depth = max(0.0, min(1.0, self.depth))
        self.phase = (self.phase + dt * 0.35) % 1.0
        self.lit = 1.0 + 0.05*math.sin(self.t*1.9) + random.uniform(-0.02, 0.02)
        facing = 1.0 if math.sin(self.yaw) >= 0 else -1.0
        self.s.update(pos=self.pos, depth=self.depth,
                      yawx=math.cos(self.yaw), yawy=math.sin(self.yaw),
                      phase=self.phase, lit=self.lit, facing=facing)
        return self.s


# ---------------------------------------------------------------------------
# Ballroom: several dancers at once (the Haunted Mansion look).
# Three slots, each streamed as pos/depth/yaw/phase/lit. `lit` doubles as the
# materialize/dematerialize fade, so dancers appear out of nothing and vanish.
# ---------------------------------------------------------------------------
BALLROOM_SLOTS = 3
BALLROOM_NEUTRAL = {}
BALLROOM_LERP = {}
for _i in range(BALLROOM_SLOTS):
    BALLROOM_NEUTRAL.update({f"pos{_i}": 0.5, f"depth{_i}": 0.5, f"yawx{_i}": 1.0,
                             f"yawy{_i}": 0.0, f"phase{_i}": 0.0, f"lit{_i}": 0.0})
    BALLROOM_LERP.update({f"pos{_i}": 0.9, f"depth{_i}": 0.6, f"yawx{_i}": 0.5,
                          f"yawy{_i}": 0.5, f"phase{_i}": 0.95, f"lit{_i}": 0.35})


class _Dancer:
    """One waltzing ghost: glides between waypoints in x/depth, twirls while it
    dances, and cycles through appear -> dance -> fade -> gone."""

    def __init__(self, seed, speed):
        r = random.Random(seed)
        self.r = r
        self.pos = r.uniform(0.1, 0.9)
        self.depth = r.uniform(0.25, 0.8)
        self.yaw = r.uniform(-math.pi, math.pi)
        self.phase = r.random()
        self.speed = speed
        self.tx, self.tz = self.pos, self.depth
        self.lit = 0.0
        self.lit_t = 0.0
        self.state = "gone"
        self.timer = r.uniform(0.5, 6.0)
        self.twirl = r.choice([-1.0, 1.0]) * r.uniform(0.5, 1.1)
        self.paused = 0.0
        self._new_waypoint()

    def _new_waypoint(self):
        self.tx = self.r.uniform(-0.05, 1.05)
        self.tz = self.r.uniform(0.15, 0.85)

    def update(self, dt, watched):
        # ---- appearance cycle ----
        self.timer -= dt
        if self.state == "gone" and self.timer <= 0:
            self.state = "in"; self.timer = 2.5
            self.pos = self.r.uniform(0.05, 0.95); self.depth = self.r.uniform(0.2, 0.8)
            self._new_waypoint()
        elif self.state == "in" and self.timer <= 0:
            self.state = "dance"; self.timer = self.r.uniform(14.0, 30.0)
        elif self.state == "dance" and self.timer <= 0:
            self.state = "out"; self.timer = 2.5
        elif self.state == "out" and self.timer <= 0:
            self.state = "gone"; self.timer = self.r.uniform(3.0, 10.0)
        lit_target = {"in": 1.0, "dance": 1.0, "out": 0.0, "gone": 0.0}[self.state]
        self.lit_t += (lit_target - self.lit_t) * min(1.0, dt * (0.9 if self.state in ("in","out") else 3.0))

        if self.state in ("in", "dance"):
            # glide toward the waypoint, easing in and out
            if self.paused > 0:
                self.paused -= dt
            else:
                dx, dz = self.tx - self.pos, self.tz - self.depth
                dist = math.hypot(dx, dz)
                if dist < 0.02:
                    self.paused = self.r.uniform(1.5, 5.0)
                    self._new_waypoint()
                else:
                    spd = self.speed * (0.4 + 0.6*min(1.0, dist/0.25))
                    step = min(dist, spd*dt)
                    self.pos += dx/dist*step
                    self.depth += dz/dist*step
            # always twirling slowly — that's the waltz
            self.yaw += self.twirl * dt
            # 3/4-time bob
            self.phase = (self.phase + dt * 0.55) % 1.0
        if watched:
            # noticed: everyone turns to look, still swaying
            d = (0.0 - self.yaw + math.pi) % (2*math.pi) - math.pi
            self.yaw += d * min(1.0, dt*1.5)
        self.depth = max(0.0, min(1.0, self.depth))
        # a little flicker, like a projection
        flick = 1.0 + 0.04*math.sin(self.phase*40.0) + self.r.uniform(-0.02, 0.02)
        return dict(pos=self.pos, depth=self.depth, yawx=math.cos(self.yaw),
                    yawy=math.sin(self.yaw), phase=self.phase,
                    lit=max(0.0, min(1.0, self.lit_t*flick)))


class BallroomDirector:
    """Brain-only. Runs BALLROOM_SLOTS dancers and streams them all."""

    def __init__(self, speed=0.05, freeze_when_watched=False):
        self.s = dict(BALLROOM_NEUTRAL)
        self.dancers = [_Dancer(seed=17 + 31*i, speed=speed) for i in range(BALLROOM_SLOTS)]
        # stagger so they don't all appear together
        for i, d in enumerate(self.dancers):
            d.timer = 1.0 + i*4.0
        self.watched = False
        self.freeze_when_watched = freeze_when_watched

    def set_watched(self, watched):
        self.watched = watched

    def update(self, dt):
        w = self.watched and self.freeze_when_watched
        for i, d in enumerate(self.dancers):
            st = d.update(dt, w)
            for k, v in st.items():
                self.s[f"{k}{i}"] = v
        return self.s


# ---------------------------------------------------------------------------
# Spirit lights: wisps and orbs that fly the room in 3D. Faceless — creepy by
# BEHAVIOUR: hover, dart, rush the glass, blink out, reappear elsewhere.
# Each slot streams position (x, y, depth), velocity (for the trail) and lit.
# ---------------------------------------------------------------------------
SPIRIT_SLOTS = 5
SPIRIT_NEUTRAL = {}
SPIRIT_LERP = {}
for _i in range(SPIRIT_SLOTS):
    SPIRIT_NEUTRAL.update({f"x{_i}": 0.5, f"y{_i}": 0.5, f"z{_i}": 0.5,
                           f"vx{_i}": 0.0, f"vy{_i}": 0.0, f"vz{_i}": 0.0, f"lit{_i}": 0.0})
    SPIRIT_LERP.update({f"x{_i}": 0.85, f"y{_i}": 0.85, f"z{_i}": 0.85,
                        f"vx{_i}": 0.7, f"vy{_i}": 0.7, f"vz{_i}": 0.7, f"lit{_i}": 0.5})


class _Spirit:
    def __init__(self, seed, speed):
        r = random.Random(seed); self.r = r
        self.x, self.y, self.z = r.uniform(0.1,0.9), r.uniform(0.3,0.8), r.uniform(0.3,0.9)
        self.vx = self.vy = self.vz = 0.0
        self.speed = speed
        self.lit = 0.0; self.lit_t = 0.0
        self.mode = "gone"; self.timer = r.uniform(0.5, 5.0)
        self.tx, self.ty, self.tz = self.x, self.y, self.z
        self.jit = r.uniform(0.0, 1000.0)

    def _pick(self, near_glass=False):
        self.tx = self.r.uniform(-0.05, 1.05)
        self.ty = self.r.uniform(0.15, 0.95)
        self.tz = self.r.uniform(0.0, 0.18) if near_glass else self.r.uniform(0.2, 1.0)

    def _next_mode(self):
        roll = self.r.random()
        if roll < 0.40:
            self.mode = "hover"; self.timer = self.r.uniform(1.5, 5.0)
        elif roll < 0.72:
            self.mode = "dart";  self.timer = 6.0; self._pick()
        elif roll < 0.86:
            self.mode = "rush";  self.timer = 6.0; self._pick(near_glass=True)
        elif roll < 0.95:
            self.mode = "blink"; self.timer = self.r.uniform(0.3, 1.2)
        else:
            self.mode = "out";   self.timer = 1.5

    def update(self, dt, t, watched):
        self.timer -= dt
        if self.mode == "gone":
            if self.timer <= 0:
                self.x, self.y, self.z = self.r.uniform(0.05,0.95), self.r.uniform(0.2,0.9), self.r.uniform(0.3,1.0)
                self.mode = "in"; self.timer = 1.2
        elif self.mode == "in":
            if self.timer <= 0: self._next_mode()
        elif self.mode == "out":
            if self.timer <= 0: self.mode = "gone"; self.timer = self.r.uniform(1.0, 4.5)
        elif self.mode == "blink":
            if self.timer <= 0:
                # reappear somewhere else, instantly — the teleport
                self.x, self.y, self.z = self.r.uniform(0.05,0.95), self.r.uniform(0.2,0.9), self.r.uniform(0.2,1.0)
                self.vx = self.vy = self.vz = 0.0
                self._next_mode()
        else:
            if self.mode == "hover":
                # near-still with a nervous jitter and slow breathing drift
                j = self.jit + t*1.7
                nx = self.x + 0.05*math.sin(j*1.3) + 0.03*math.sin(j*3.1+1.0)
                ny = self.y + 0.04*math.sin(j*1.1+2.0)
                nz = self.z + 0.03*math.sin(j*0.9+4.0)
                self.vx, self.vy, self.vz = (nx-self.x)/dt*0.3, (ny-self.y)/dt*0.3, (nz-self.z)/dt*0.3
                self.x, self.y, self.z = self.x*0.7+nx*0.3, self.y*0.7+ny*0.3, self.z*0.7+nz*0.3
                if self.timer <= 0: self._next_mode()
            else:   # dart / rush: accelerate hard toward the target, ease in at the end
                dx, dy, dz = self.tx-self.x, self.ty-self.y, self.tz-self.z
                dist = math.sqrt(dx*dx+dy*dy+dz*dz)
                spd = self.speed * (7.0 if self.mode == "rush" else 4.5)
                spd *= 0.25 + 0.75*min(1.0, dist/0.30)
                if dist < 0.02 or self.timer <= 0:
                    # arrived: rushes hang at the glass a beat, then retreat
                    if self.mode == "rush" and self.r.random() < 0.6:
                        self.mode = "hover"; self.timer = self.r.uniform(0.6, 2.0)
                    else:
                        self._next_mode()
                else:
                    step = min(dist, spd*dt)
                    ux, uy, uz = dx/dist, dy/dist, dz/dist
                    self.x += ux*step; self.y += uy*step; self.z += uz*step
                    self.vx, self.vy, self.vz = ux*spd, uy*spd, uz*spd
        if watched and self.mode in ("hover", "dart"):
            # they notice: drift toward the glass and freeze
            self.z += (0.05 - self.z) * min(1.0, dt*0.5)
        target = 0.0 if self.mode in ("gone", "out", "blink") else 1.0
        rate = 6.0 if self.mode == "blink" else (1.4 if self.mode in ("in","out") else 3.0)
        self.lit_t += (target - self.lit_t) * min(1.0, dt*rate)
        self.y = max(0.05, min(1.0, self.y)); self.z = max(0.0, min(1.0, self.z))
        flick = 0.85 + 0.15*math.sin(t*23.0 + self.jit) * (1.0 if self.r.random() < 0.3 else 0.0)
        return dict(x=self.x, y=self.y, z=self.z, vx=self.vx, vy=self.vy, vz=self.vz,
                    lit=max(0.0, min(1.0, self.lit_t*flick)))


class SpiritDirector:
    """Brain-only. Runs SPIRIT_SLOTS spirit lights and streams them all."""

    def __init__(self, speed=0.06, freeze_when_watched=False):
        self.s = dict(SPIRIT_NEUTRAL)
        self.spirits = [_Spirit(seed=101+53*i, speed=speed) for i in range(SPIRIT_SLOTS)]
        for i, sp in enumerate(self.spirits):
            sp.timer = 0.5 + i*2.5
        self.watched = False
        self.freeze_when_watched = freeze_when_watched
        self.t = 0.0

    def set_watched(self, watched):
        self.watched = watched

    def update(self, dt):
        self.t += dt
        w = self.watched and self.freeze_when_watched
        for i, sp in enumerate(self.spirits):
            for k, v in sp.update(dt, self.t, w).items():
                self.s[f"{k}{i}"] = v
        return self.s
