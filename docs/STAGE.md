# Stage mode

`--mode stage` stops treating the projectors as two separate eyes and makes them
**viewports onto one continuous virtual stage** wider than any single window.
The brain streams one figure position along that stage; each Pi renders whatever
falls inside its own span and black otherwise.

```
stage:   0.0 ......................................................... 1.0
window1: [0.00 -- 0.40]              window2:          [0.60 -- 1.00]
                      \____ solid wall ____/
```

A figure walks the stage, appears in window 1, leaves its right edge, crosses
the gap of real wall, then enters window 2 — so it reads as something moving
*through the house*. **The gap is the effect:** vanishing at one window and
reappearing at the next sells "it's behind the wall" far better than anything
that happens inside a single frame.

Figure types (`--eye` in stage mode):

| Type | Look | Needs a lit pane? |
|---|---|---|
| `silhouette` | procedural walking figure, dark against a lit window | yes (drawn for you) |
| `sprite` | pre-rendered walk cycle from an atlas | yes (drawn for you) |
| `ghost` | translucent hooded spectre: layered vapour, ragged shroud, hollow face | **no** — it glows |
| `mansion` | **the ballroom**: three Victorian ghosts waltzing at depth, materializing and fading | **no** — Pepper's Ghost green |
| `spirits` | **spirit lights**: faceless orbs and wisps flying the room in true 3D — hover, dart, rush the glass, blink out and reappear | **no** — they glow |

**`spirits` sidesteps faces entirely.** Five spirit lights — orbs with a
white-hot core and a trail that streaks behind them, and wisps of writhing
smoke — fly the room in true 3D (x, height *and* depth), so they shrink, dim
and haze as they recede. They're creepy by **behaviour**, not appearance: they
hover with a nervous jitter, dart suddenly, rush straight at the glass and hang
there, blink out and reappear somewhere else. Cold palettes (pale blue, dead
white, sick green). All five are streamed and synced.

**Trails follow the flight path.** Each renderer keeps a short history of
where its spirits actually were and draws the trail through those points, so
it bends round every curve instead of shooting off straight along the current
velocity. A blink resets the history rather than smearing a line across the
room. Trail length is `SPIRIT_HIST_N` x `SPIRIT_HIST_DT` in `demoneye.py`. With
`freeze_when_watched` they drift toward the glass and go still.

```bash
./venv/bin/python demoneye.py --role brain --mode stage --eye spirits --windowed --solo
```

**`mansion` is the Haunted-Mansion effect.** Three dancers at once — a
top-hatted gentleman with a cane, a bonneted lady in a bell gown, another
gentleman — in Pepper's-Ghost green-white, semi-transparent with a faint
projection shimmer. They glide between points in x *and depth*, twirl slowly as
they waltz with a lilting 3/4 rise and fall, and cycle through **materialize →
dance → fade → gone**, staggered so the ballroom fills gradually. All three are
streamed and synced, so the pair of windows shows one continuous ballroom. With
`freeze_when_watched` they all turn to look at whoever's watching.

```bash
./venv/bin/python demoneye.py --role brain --mode stage --eye mansion --windowed --solo
```

**The ghost moves in 3D.** It wanders a room behind the windows — drifting
between points in x *and depth*, turning to face the way it travels, pausing,
and sometimes turning to stare straight out of the glass. Distance is rendered
as real perspective: smaller, higher toward the horizon, dimmer and hazier. It
turns properly too: foreshortened in profile, the hollow face sliding round and
fading as it turns away, a denser dark hood from behind. With
`freeze_when_watched` on, being noticed makes it **stop and turn to face you**.
The ghost ignores `figure_stride` (nothing is footfall-driven) and wants a
slower `speed` than a walker — around `0.05`.

The first two are **occluders**: the shader lights the window pane and the figure
blocks it, so it reads as someone backlit inside. The ghost is **emissive** — it
glows against pure black like the eyes do, so it reads as something drifting
past dark windows, and the house around it stays dark. It bobs and sways on its
own, so it never looks static even when the figure pauses.

```bash
./venv/bin/python demoneye.py --role brain --mode stage --eye ghost --windowed --solo
```

Run it (same on both Pis, only the span differs):
```bash
./venv/bin/python demoneye.py --role brain    --mode stage --eye silhouette
./venv/bin/python demoneye.py --role follower --mode stage --eye silhouette
```

Per-Pi span in `config.ini`:
```ini
[stage]
; this window's slice of the stage
stage_from = 0.00
stage_to   = 0.40
; figure height as a fraction of the window
figure_scale = 0.72
; stage units per second
speed = 0.085
freeze_when_watched = false
```

Set the spans to roughly match where the windows sit on your house, and leave a
gap between them about as wide as the real wall.

**A dark figure on black is invisible**, so the shader lights the pane: a warm
glow fills the window and the figure occludes it, with a thin rim of light
leaking around the body. That's what makes it read as someone backlit inside.

### Using Blender / Mixamo figures instead of the procedural one

The built-in silhouette is drawn with maths. For a better figure, render a walk
cycle to PNG frames and pack it into a sprite atlas:

```bash
python sprite_atlas.py frames/ figures/shambler       # frames/ = your PNG sequence
```

Then point `config.ini` at it and stage mode switches to the sprite shader
automatically:
```ini
[stage]
figure_atlas = figures/shambler.png
```

A hunched `figures/shambler.png` ships with the project so it works out of the box.

Getting a figure: **Mixamo** (free with an Adobe account) auto-rigs a humanoid
and has a large motion-capture library; its characters and animations are
royalty-free for personal, commercial and non-profit use. CC0 alternatives:
the Quaternius Universal Animation Library, and rig-ready characters from
KayKit, Quaternius, PolygonalMind and Kenney. **Don't use assets ripped from
commercial games** — they're copyrighted, and a projection facing the street is
public display.

Two settings matter once you have real frames:

```ini
; which way the SOURCE render walks
figure_faces  = left
; stage distance covered by ONE full walk cycle
figure_stride = 0.075
```

`figure_faces` stops the figure moonwalking: the shader only mirrors the art
when the travel direction differs from the direction it was rendered facing.

`figure_stride` keeps the feet planted. The walk cycle advances by **distance
travelled**, not by time, so one cycle always covers `figure_stride` of the
stage no matter what `speed` is set to — change the speed and the legs keep up
automatically. Tune it by eye: if the figure slides forward like it's on ice,
lower it; if the legs churn faster than it travels, raise it.

In Blender: orthographic camera straight on, character walking in place,
transparent film background, flat black emission material (only the alpha
matters), square output like 256x256, and render the whole cycle — 24-32 frames
loops smoothly. The packer trims all frames to a common bounding box so the
feet stay put between frames.

**`freeze_when_watched`** is the good one: with the mmWave bridge running, the
figure only moves while *nobody* is detected and stops dead the moment someone
is. It never appears to move while being watched — people will swear it changed
position when they looked away.

---
