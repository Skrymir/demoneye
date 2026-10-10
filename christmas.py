"""
The Christmas scene: snow that gusts when someone walks past, and Santa's
sleigh flying across both projectors.

Same shape as the eyes (see renderers.py): the brain's ChristmasDirector
streams a small state 60 times a second, both Pis draw it with CHRISTMAS_FRAG.
The difference is that the two projectors show two halves of ONE wide world:
the sleigh flies off the left projector and onto the right one.

World units: 1 = one screen height. Each projector shows a 16:9 window of the
world; their centres are SEPARATION apart. Which window a Pi shows comes from
its hostname (pi-eye-left -> left), or CHRISTMAS_SIDE=left|right|both in the
environment. Anything else (your Mac) shows "both": the whole world, zoomed
out, so you can watch the full flight in one window.

    ./venv/bin/python demoneye.py --role brain --solo --windowed --eye christmas
"""

import math
import os
import random
import time

import settings as S

ASPECT = 16 / 9            # each projector's picture
SEPARATION = 3.0           # distance between the two projectors' centres (screen heights)
SPAN = SEPARATION / 2 + ASPECT / 2      # world x reaches -SPAN..+SPAN
SLEIGH_SPEED = 0.75        # world units per second
SLEIGH_MARGIN = 0.8        # start/finish this far beyond the outer edges
SLEIGH_COOLDOWN = 20.0     # s after a flight before a passer-by can launch another
IDLE_FLIGHT = (60.0, 150.0)  # s between flights when nobody's around
FIRST_FLIGHT = 4.0         # s after start (so a preview shows one straight away)

# Streamed state. The sleigh keys aren't smoothed (lerp 1): it moves steadily
# at 60 Hz already, and smoothing would drag it across the screen when a new
# flight jumps it back to the start.
NEUTRAL = {"wind": 0.0, "gust_x": 0.0, "gust": 0.0,
           "sleigh_x": -10.0, "sleigh_y": 0.2, "sleigh_on": 0.0, "sleigh_dir": 1.0}
LERP = {"wind": 0.05, "gust_x": 0.2, "gust": 0.08,
        "sleigh_x": 1.0, "sleigh_y": 1.0, "sleigh_on": 1.0, "sleigh_dir": 1.0}


class ChristmasDirector:
    """Brain only. Wind, the passer-by's gust, and when the sleigh flies."""

    def __init__(self, rng=None, clock=time.monotonic):
        self.rng = rng or random.Random()
        self.clock = clock
        self.s = dict(NEUTRAL)
        self.t = 0.0
        # (x, y, deadline) from the MQTT thread, read once per frame (as the eyes)
        self.track = None
        self.tracking = False
        self.person_x = 0.0      # world x of the passer-by
        self.vel = 0.0           # their smoothed sideways speed (world units/s)
        self.flying = False
        self.last_flight_end = -1e9
        self.next_idle = FIRST_FLIGHT
        self.mood = "snow"       # for --stats

    # -- targets (same contract as the eye Director) --
    def set_target(self, x, y, hold):
        self.track = (max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)),
                      self.clock() + max(0.1, hold))

    def clear_target(self):
        self.track = None

    # -- the sleigh --
    def launch(self, direction):
        d = 1.0 if direction >= 0 else -1.0
        self.flying = True
        self.s["sleigh_dir"] = d
        self.s["sleigh_x"] = -d * (SPAN + SLEIGH_MARGIN)
        self.s["sleigh_y"] = self.rng.uniform(0.08, 0.28)
        self.s["sleigh_on"] = 1.0

    def update(self, dt):
        self.t += dt
        s = self.s
        track = self.track
        if track is not None and self.clock() >= track[2]:
            track = None

        if track is not None:
            x = track[0] * SPAN
            if not self.tracking:                  # someone just arrived
                self.tracking = True
                self.person_x = x
                self.vel = 0.0
                if not self.flying and self.t - self.last_flight_end >= SLEIGH_COOLDOWN:
                    # fly in the direction they're walking, or toward them
                    self.launch(x if abs(x) > 0.1 else self.rng.choice((-1.0, 1.0)))
            else:
                v = (x - self.person_x) / max(dt, 1e-3)
                self.vel += (v - self.vel) * min(1.0, dt * 4.0)
                self.person_x = x
            gust_target = 1.0
            s["gust_x"] += (self.person_x - s["gust_x"]) * min(1.0, dt * 6.0)
        else:
            self.tracking = False
            self.vel *= max(0.0, 1.0 - dt * 2.0)
            gust_target = 0.0

        # a gentle breeze that wanders, pushed along by whoever is walking past
        breeze = 0.18 * math.sin(self.t * 0.11) + 0.08 * math.sin(self.t * 0.37 + 1.3)
        wind_target = max(-1.0, min(1.0, breeze + 1.2 * self.vel))
        s["wind"] += (wind_target - s["wind"]) * min(1.0, dt * 1.5)
        s["gust"] += (gust_target - s["gust"]) * min(1.0, dt * (2.5 if gust_target else 0.6))

        if self.flying:
            s["sleigh_x"] += s["sleigh_dir"] * SLEIGH_SPEED * dt
            if s["sleigh_x"] * s["sleigh_dir"] > SPAN + SLEIGH_MARGIN:
                self.flying = False
                s["sleigh_on"] = 0.0
                self.last_flight_end = self.t
                self.next_idle = self.t + self.rng.uniform(*IDLE_FLIGHT)
        elif self.t >= self.next_idle:
            self.launch(self.rng.choice((-1.0, 1.0)))

        self.mood = "sleigh" if self.flying else ("gust" if self.tracking else "snow")
        return s


def view_for(side, aspect):
    """(world x at the screen's centre, world half-width) for this projector."""
    if side == "left":
        return -SEPARATION / 2, aspect / 2
    if side == "right":
        return SEPARATION / 2, aspect / 2
    return 0.0, SPAN                               # both: the whole world


def resolve_side():
    side = os.environ.get("CHRISTMAS_SIDE", "").strip().lower()
    if side in ("left", "right", "both"):
        return side
    try:
        import ha_status
        side = ha_status.resolve_node(S.STATUS_NODE)
    except Exception:
        side = ""
    return side if side in ("left", "right") else "both"


CHRISTMAS_FRAG = """\
uniform vec2  iResolution;
uniform float iTime;
uniform vec2  uView;      // world x at the screen centre, world half-width shown
uniform float uDrift;     // sideways snow drift so far (wind, integrated)
uniform float uWind;      // -1..1: slants the falling snow
uniform vec2  uGust;      // world x of the passer-by, gust strength 0..1
uniform float uSwirl;     // swirl angle so far around the passer-by
uniform vec3  uSleigh;    // world x, flight altitude, visible 0/1
uniform float uSleighDir; // +1 flying right, -1 left
out vec4 fragColor;

float segd(vec2 p, vec2 a, vec2 b){
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return length(pa - ba * h);
}
float elld(vec2 p, vec2 r){ return (length(p / r) - 1.0) * min(r.x, r.y); }
float rboxd(vec2 p, vec2 b, float r){
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
float pathY(float x){ return uSleigh.y + 0.04 * sin(x * 1.7); }

// One layer of falling snow. layer 0 = near (big, fast), 3 = far (small, slow).
float snowLayer(vec2 w, float layer){
    float k = layer / 3.0;
    float sc = mix(5.0, 15.0, k);                  // cells per world unit
    vec2 p = w;
    p.x += uDrift * mix(1.0, 0.45, k) + uWind * 0.25 * w.y;
    p.y += iTime * mix(0.20, 0.08, k);
    p *= sc;
    vec2 id = floor(p), f = fract(p);
    float h = hash21(id + layer * 17.0);
    if (h > mix(0.35, 0.75, k)) return 0.0;        // not every cell has a flake
    vec2 c = vec2(0.3 + 0.4 * hash21(id + 3.1), 0.3 + 0.4 * hash21(id + 7.7));
    c.x += 0.15 * sin(iTime * (0.6 + h) + h * 6.2832);
    float r = mix(0.11, 0.06, k) * (0.6 + 0.5 * hash21(id + 11.0));
    float b = smoothstep(r, r * 0.25, length(f - c));
    return b * mix(1.0, 0.4, k);
}

// paint a filled shape (signed distance d) in colour c, antialiased over px
void fill(inout vec3 col, float d, vec3 c, float px){
    col = mix(col, c, smoothstep(px, -px, d));
}

vec3 sleigh(vec3 col, vec2 w, float px){
    float sx = uSleigh.x;
    vec2 q = (w - vec2(sx, pathY(sx))) / 0.9;      // local units: sleigh ~0.2 long
    px /= 0.9;
    q.x *= uSleighDir;                             // the team always leads at +x
    if (abs(q.x - 0.17) > 0.42 || abs(q.y) > 0.16) return col;

    float t = iTime;
    vec3 gold = vec3(1.0, 0.80, 0.42), red = vec3(0.85, 0.08, 0.06);
    vec3 deer = vec3(0.62, 0.43, 0.26), white = vec3(0.95, 0.95, 0.92);

    // a soft golden glow around the whole team
    float team = min(rboxd(q - vec2(0.17, 0.02), vec2(0.30, 0.05), 0.05), 1.0);
    col += gold * 0.10 * exp(-max(team, 0.0) * 30.0);

    // reins from the sleigh to the lead reindeer
    float rein = segd(q, vec2(0.08, 0.03), vec2(0.50, 0.035 + 0.006 * sin(t * 9.0)));
    col = mix(col, gold, smoothstep(px * 1.5, 0.0, rein) * 0.8);

    // four reindeer, galloping
    for (int i = 0; i < 4; i++){
        float fi = float(i);
        vec2 o = vec2(0.20 + 0.105 * fi, 0.02 + 0.012 * sin(t * 8.0 + fi * 1.7));
        vec2 r = q - o;
        float ph = t * 10.0 + fi * 1.3;
        float body = elld(r, vec2(0.040, 0.016));
        float neck = segd(r, vec2(0.030, 0.005), vec2(0.046, 0.026)) - 0.006;
        float head = elld(r - vec2(0.054, 0.028), vec2(0.012, 0.008));
        float legs = min(min(segd(r, vec2( 0.025, -0.010), vec2( 0.032 + 0.012 * sin(ph), -0.040)),
                             segd(r, vec2( 0.018, -0.010), vec2( 0.022 + 0.012 * sin(ph + 0.6), -0.040))),
                         min(segd(r, vec2(-0.025, -0.010), vec2(-0.032 - 0.012 * sin(ph), -0.040)),
                             segd(r, vec2(-0.018, -0.010), vec2(-0.022 - 0.012 * sin(ph + 0.6), -0.040)))) - 0.0035;
        float antl = min(min(segd(r, vec2(0.050, 0.034), vec2(0.040, 0.058)),
                             segd(r, vec2(0.044, 0.048), vec2(0.032, 0.052))),
                         segd(r, vec2(0.056, 0.034), vec2(0.062, 0.056))) - 0.0025;
        fill(col, min(min(body, neck), min(head, legs)), deer, px);
        fill(col, antl, vec3(0.85, 0.75, 0.55), px);
        if (i == 3){                               // Rudolph
            float nose = length(r - vec2(0.067, 0.027));
            col += vec3(1.0, 0.12, 0.08) * (smoothstep(0.008, 0.004, nose)
                    + 0.5 * exp(-nose * 60.0) * (0.75 + 0.25 * sin(t * 3.0)));
        }
    }

    // the sleigh: runner, curled front, red body with gold trim
    float runner = min(segd(q, vec2(-0.085, -0.050), vec2(0.085, -0.050)),
                       abs(length(q - vec2(0.085, -0.030)) - 0.020)) - 0.004;
    fill(col, runner, gold, px);
    float body = min(rboxd(q - vec2(0.0, -0.005), vec2(0.080, 0.032), 0.016),
                     elld(q - vec2(0.072, 0.022), vec2(0.020, 0.028)));
    fill(col, body, red, px);
    col = mix(col, gold, smoothstep(px * 1.5, 0.0, abs(body)) * 0.9);

    // the sack, and Santa
    fill(col, elld(q - vec2(-0.062, 0.040), vec2(0.026, 0.026)), vec3(0.45, 0.30, 0.16), px);
    fill(col, elld(q - vec2(-0.010, 0.040), vec2(0.024, 0.030)), red, px);
    fill(col, rboxd(q - vec2(-0.010, 0.022), vec2(0.024, 0.004), 0.002), white, px);
    fill(col, length(q - vec2(-0.004, 0.078)) - 0.014, vec3(0.98, 0.80, 0.68), px);
    fill(col, elld(q - vec2(-0.001, 0.068), vec2(0.014, 0.010)), white, px);      // beard
    fill(col, segd(q, vec2(-0.012, 0.090), vec2(-0.030, 0.100)) - 0.007, red, px);  // hat
    fill(col, length(q - vec2(-0.034, 0.100)) - 0.006, white, px);
    return col;
}

vec3 trail(vec3 col, vec2 w){
    float behind = (w.x - uSleigh.x) * -uSleighDir;     // distance behind the sleigh
    if (behind < 0.0 || behind > 1.8) return col;
    float py = pathY(w.x) - 0.015 - behind * 0.05;      // the dust drifts down
    float spread = 0.012 + behind * 0.05;
    float dy = w.y - py;
    float fade = (1.0 - behind / 1.8);
    fade *= fade;
    float band = exp(-dy * dy / (spread * spread));
    vec2 cell = floor(w * 70.0);
    float h = hash21(cell);
    vec2 f = fract(w * 70.0) - 0.5;
    float star = step(0.82, h) * smoothstep(0.25, 0.0, length(f))
               * (0.5 + 0.5 * sin(iTime * 9.0 + h * 50.0));
    vec3 gold = vec3(1.0, 0.82, 0.45);
    return col + gold * fade * band * (0.12 + 1.2 * star);
}

void main(){
    float px = 2.0 * uView.y / iResolution.x;           // world units per pixel
    vec2 w = vec2(uView.x + (gl_FragCoord.x - 0.5 * iResolution.x) * px,
                  (gl_FragCoord.y - 0.5 * iResolution.y) * px);

    // flakes near the passer-by eddy around in little loops. A displacement,
    // not a rotation of space: rotating stretched the flakes into streaks.
    vec2 g = w - vec2(uGust.x, -0.15);
    float fo = exp(-dot(g, g) * 2.0) * uGust.y;
    vec2 ws = w - fo * 0.16 * vec2(cos(uSwirl + w.y * 1.5), sin(uSwirl * 1.3 + w.x * 1.5));

    float s = 0.0;
    for (int i = 0; i < 4; i++) s += snowLayer(ws, float(i));
    vec3 col = vec3(0.86, 0.92, 1.0) * min(s, 1.0);

    // a low snowbank along the bottom of each picture
    float bottom = -0.5 * iResolution.y * px;
    float bank = bottom + 0.035 + 0.025 * fbm4(vec2(w.x * 2.5, 0.0));
    col = mix(col, vec3(0.55, 0.62, 0.72), smoothstep(px * 2.0, -px * 2.0, w.y - bank));

    if (uSleigh.z > 0.5){
        col = trail(col, w);
        col = sleigh(col, w, px);
    }
    fragColor = vec4(col, 1.0);
}
"""


def _base_renderer():
    from renderers import EyeRenderer
    return EyeRenderer


class ChristmasRenderer(_base_renderer()):
    """Snow + sleigh. Reuses the eye renderer's GL plumbing (bind/u1/u2)."""
    uniforms = ("iResolution", "iTime", "uView", "uDrift", "uWind", "uGust",
                "uSwirl", "uSleigh", "uSleighDir")
    schema = NEUTRAL
    lerp = LERP

    def __init__(self, side=None):
        self.name = "christmas"
        self.frag = CHRISTMAS_FRAG
        self.prog = None
        self._uloc = {}
        self.side = side or resolve_side()
        self._last_t = None
        self.drift = 0.0       # integrated here from the streamed wind, so the
        self.swirl = 0.0       # snow keeps moving smoothly between messages

    def make_director(self):
        return ChristmasDirector()

    def set_uniforms(self, app, s):
        from OpenGL import GL
        dt = 0.0 if self._last_t is None else max(0.0, min(0.1, app.t - self._last_t))
        self._last_t = app.t
        self.drift += s["wind"] * 0.35 * dt
        self.swirl += s["gust"] * 2.2 * dt
        cx, hw = view_for(self.side, app.W / max(1, app.H))
        self.u2("iResolution", app.W, app.H)
        self.u1("iTime", app.t)
        self.u2("uView", cx, hw)
        self.u1("uDrift", self.drift)
        self.u1("uWind", max(-1.0, min(1.0, s["wind"])))
        self.u2("uGust", s["gust_x"], max(0.0, min(1.0, s["gust"])))
        self.u1("uSwirl", self.swirl)
        loc = self._uloc.get("uSleigh", -1)
        if loc != -1:
            GL.glUniform3f(loc, float(s["sleigh_x"]), float(s["sleigh_y"]),
                           1.0 if s["sleigh_on"] > 0.5 else 0.0)
        self.u1("uSleighDir", 1.0 if s["sleigh_dir"] >= 0 else -1.0)

    def stats_label(self, app, s):
        return f"[{app.director.mood}]" if app.director else f"[{self.side}]"
