# The eyes

## Eye types

Pick which eye to render with `--eye`:

- `--eye demon` — a dragon-style burning eye: glowing slit-pupil iris
  set in a shaped reptilian lid aperture, with scaled hide faintly lit by its
  own fire. The lids animate to show **emotion** — idle glares and wide flares,
  a surprise flare when it acquires a tracked target, then a narrowed glare
  while it follows. Blinks close along the curved lids.
- `--eye human` — a pale bloodshot human eyeball set in fleshy lids: veined
  sclera, colored iris, round pupil, lash line and fold crease. Emotions flare
  it into a wide horror stare or hood it into a glare.
- `--eye walker` (default) — a frozen undead eye: a luminous crystalline ice-blue iris
  with a pinprick pupil, sunk in a deeply shadowed socket, surrounded by pale
  crevassed frost-flecked skin. Same emotions, run cold.

Set the **same** `--eye` on both Pis so the pair matches. Everything else —
sync, gaze, blink, dilation, and the mirror flags — works identically across
types. Per-type look settings live in their own blocks in `demoneye.py`
(`DEMON eye` / `HUMAN eye`); the human iris color, vein count, and sclera tint
are all there.

## Tuning the look
Each eye is a GLSL fragment shader in **`eye_shaders.py`** — that's where all
appearance tuning now lives (edit the shader strings, no recompile step, just
restart). Placement and size stay in `config.ini` (`center_x/y`, `scale`).

Demon shader (`DEMON_FRAG`):
- Iris colors: the `core` / `edge` vectors. Halo: the `vec3(1.0,0.12,0.05)` term.
- Slit: `Wp` / `k` (pointiness), the `open_amt` rounding, and the molten rim's
  two `mix(... clamp(...))` lines.

Human shader (`HUMAN_FRAG`):
- Iris colors: `inC` / `outC` and the `limbal` mix.
- Bloodshot vessels: the two `smoothstep(...abs(vf1/vf2-0.5))` terms and the
  `vec3(0.74,0.17,0.14)` vein color; `fromCorner`/`taper` control spread.
- Roundness: the light `L`, eyeball radius `Rg`, and the `form` contrast.
- Wetness: the `wet * 0.20` moist-rim term.

Colors in GLSL are 0–1 floats (so 128/255 ≈ 0.5). Animation timing (blink rate,
saccade speed, dilation, the `WIDEN_CHANCE` startle) is in the `Director` class
in `demoneye.py` — the brain owns it, so edit it only on the brain Pi.

## A matched pair of eyes (mirroring)

By default both projectors show the *same* eye moving the *same* way — reads as
one creature scanning. To make them a symmetric **pair** on a face, two per-Pi
flags (set them on the follower / right-hand eye):

- `MIRROR = True` — flips the eye horizontally so the glint lands on the opposite
  side. With the brain eye on the left of the house and this one on the right,
  the highlights point symmetrically like a real face. The eyes still track
  together (both look left at the same time).
- `MIRROR_GAZE = True` — additionally flips the *look direction*, so the eyes
  diverge and converge (a cross-eyed, independent-feeling pair). Creepier, but
  can look wall-eyed — leave it off if you want deliberate, in-sync tracking.

Both flags are local rendering only; they don't affect sync. Recommended pair
setup: `MIRROR = True`, `MIRROR_GAZE = False` on the right-hand projector.
