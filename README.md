# Demon Eye — Synchronized Halloween Projection

Two Raspberry Pi 5s, one projector each, projecting a giant animated eye onto
your house — a glowing red demon eye or a pale bloodshot human eye (`--eye`).
Both eyes are drawn by GPU fragment shaders (GLSL, via PyOpenGL). One Pi (the
**brain**) directs the animation and streams the eye's state over MQTT ~50x/sec;
both Pis render that state, easing toward it every frame. Because both apply
identical smoothing to the same stream, the two eyes move as one — no clock
sync, and a dropped packet self-heals on the next one.

The whole illusion rests on one thing: **the eye is drawn on pure black.** Black
is no light out of the projector, so the house stays dark and only the eye glows.

---

## 1. Install (do this on BOTH Pis)

```bash
mkdir -p ~/demoneye && cd ~/demoneye
# copy demoneye.py, eye_shaders.py, config.ini, requirements.txt, demoneye.service here
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 2. Configure

All per-machine settings live in **`config.ini`** (next to `demoneye.py`) — so
dropping in a new `demoneye.py` never overwrites your broker details or
alignment. Edit that file:

```ini
[mqtt]
host = 192.168.1.10      ; your Home Assistant / Mosquitto box
port = 1883
username =               ; leave blank if the broker allows anonymous
password =
topic = halloween/eyes/state

[display]
center_x = 0.50          ; align THIS Pi's eye to what it projects onto
center_y = 0.50
scale    = 1.00
mirror       = false     ; true on ONE projector for a symmetric pair
mirror_gaze  = false
gaze_scale   = 1.8       ; how far the gaze travels (raise so tracking is obvious)
gaze_scale_y = 1.3
```

Both Pis must share the same `[mqtt]` section. The `[display]` section is
per-Pi. Everything about the *animation* comes from the brain over MQTT, so you
only tune the look once (in `demoneye.py`).

Notes:
- Put comments on their own line — an inline comment after a value gets read as
  part of the value.
- `MQTT_USER` / `MQTT_PASS` environment variables, and `--host`, still override
  the file if set (handy for keeping a password out of the file).
- `--config /path/to/other.ini` points at a different file.

## Stage mode — a figure crossing between windows

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
stage_from = 0.00      ; this window's slice of the stage
stage_to   = 0.40
figure_scale = 0.72    ; figure height as a fraction of the window
speed = 0.085          ; stage units per second
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
figure_faces  = left     ; which way the SOURCE render walks
figure_stride = 0.075    ; stage distance covered by ONE full walk cycle
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

## Eye types

Pick which eye to render with `--eye`:

- `--eye demon` (default) — a dragon-style burning eye: glowing slit-pupil iris
  set in a shaped reptilian lid aperture, with scaled hide faintly lit by its
  own fire. The lids animate to show **emotion** — idle glares and wide flares,
  a surprise flare when it acquires a tracked target, then a narrowed glare
  while it follows. Blinks close along the curved lids.
- `--eye human` — a pale bloodshot human eyeball set in fleshy lids: veined
  sclera, colored iris, round pupil, lash line and fold crease. Emotions flare
  it into a wide horror stare or hood it into a glare.
- `--eye walker` — a frozen undead eye: a luminous crystalline ice-blue iris
  with a pinprick pupil, sunk in a deeply shadowed socket, surrounded by pale
  crevassed frost-flecked skin. Same emotions, run cold.

Set the **same** `--eye` on both Pis so the pair matches. Everything else —
sync, gaze, blink, dilation, and the mirror flags — works identically across
types. Per-type look settings live in their own blocks in `demoneye.py`
(`DEMON eye` / `HUMAN eye`); the human iris color, vein count, and sclera tint
are all there.

## 3. Test before you mount anything

On the brain Pi (or any desktop), run a self-contained preview — no MQTT needed:

```bash
./venv/bin/python demoneye.py --role brain --windowed --solo
# or the bloodshot human eye:
./venv/bin/python demoneye.py --role brain --windowed --solo --eye human
```

You should see the eye look around, blink, and its pupil dilate. Press `Esc`/`q`
to quit.

Then test the real two-Pi sync on your bench:

```bash
# Pi A
./venv/bin/python demoneye.py --role brain --windowed
# Pi B
./venv/bin/python demoneye.py --role follower --windowed
```

Both windows should move together. (`--host 192.168.x.x` overrides the broker
on the command line if you want to test without editing the file.)

## 4. Run it on the projectors (KMSDRM, no desktop)

For a decoration you just want to power on, boot each Pi to the console and let
it draw straight to HDMI:

1. `sudo raspi-config` → **System Options → Boot / Auto Login → Console** (or
   Console Autologin). No desktop — a running desktop would hold the display and
   block KMSDRM.
2. Install the GL runtime libs (Lite is minimal and won't have these), and make
   sure your user can reach the GPU:
   ```bash
   sudo apt install -y libgles2 libegl1 libgbm1 libgl1-mesa-dri
   sudo usermod -aG video,render pi
   ```
3. Stop the screen from blanking: add `consoleblank=0` to the end of the single
   line in `/boot/firmware/cmdline.txt`, then reboot.
4. Install the service (edit `--role` first — **brain** on one Pi, **follower**
   on the other; set `User=` if not `joshc`):

   ```bash
   sudo cp demoneye.service /etc/systemd/system/demoneye.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now demoneye
   journalctl -u demoneye -f   # watch it start / see the GL + MQTT lines
   ```

Now both eyes come up on boot and self-sync as soon as the brain connects.

> **Service won't start: "kmsdrm not available".** The app runs fine from an SSH
> prompt but fails as a service. That's a service-context problem, in order of
> likelihood:
> 1. **A desktop is running and holds the display.** `systemctl get-default` —
>    if it says `graphical.target`, switch to console:
>    `sudo systemctl set-default multi-user.target && sudo reboot`
>    (or `sudo raspi-config` → System Options → Boot/Auto Login → Console).
> 2. **The service user can't reach the GPU.** A system service isn't part of a
>    login session, so it doesn't inherit the seat's device ACLs. The shipped
>    unit sets `SupplementaryGroups=video render input` and attaches to a VT
>    (`PAMName=login`, `TTYPath=/dev/tty1`, `StandardInput=tty`) so SDL can
>    become DRM master. Make sure your copy has those lines.
> 3. **SDL picked the wrong DRM card.** A Pi 5 exposes two: a render-only V3D
>    node and the one that actually drives HDMI. SDL defaults to index 0 and
>    reports "kmsdrm not available" if that's the render node. Find the display
>    card — it's the one with connectors:
>    ```bash
>    ls /sys/class/drm/          # the display card owns cardN-HDMI-A-1
>    ```
>    then set `SDL_KMSDRM_DEVICE_INDEX` to that N (the shipped unit sets 1):
>    ```bash
>    SDL_KMSDRM_DEVICE_INDEX=1 SDL_VIDEODRIVER=kmsdrm EYE_GL=es \
>      ./venv/bin/python demoneye.py --role brain --solo
>    ```
>    Card numbering isn't guaranteed stable across hardware, so set it
>    explicitly on each Pi rather than assuming both match.
> 4. **No GPU device at all.** `ls -l /dev/dri` should show a `card*` and
>    `renderD128`.
>
> Confirm it works interactively first — that isolates the display problem from
> the service problem:
> ```bash
> SDL_VIDEODRIVER=kmsdrm EYE_GL=es ./venv/bin/python demoneye.py --role brain --solo
> ```

> **How the GL path picks a context.** The Pi's GPU exposes desktop OpenGL only
> at 3.1 but OpenGL **ES** at 3.1, so on the Pi (any non-Mac) the app requests a
> **GLES 3.0** context and loads the `#version 300 es` shaders; on macOS it uses
> a desktop 3.3 core context and `#version 330`. Same shader bodies either way.
> Under the console (KMSDRM) the app sets `PYOPENGL_PLATFORM=egl` for you (the
> service already exports `SDL_VIDEODRIVER=kmsdrm`). Force a mode with
> `EYE_GL=es` or `EYE_GL=core` if you ever need to.
>
> **First-time GL check on a Pi** (do this before wiring up the service): from
> the console, `EYE_GL=es ./venv/bin/python demoneye.py --role brain --solo`.
> It prints a `[gl] OpenGL ES ... (GLES)` line and should show the eye on HDMI.
> If it errors, paste me the output — the shaders and render path are validated,
> so anything here is environment (a missing lib or a driver quirk), and the
> fallback is to run under the desktop instead (same code, different display).

> **Prefer to run from the desktop instead?** Skip KMSDRM: remove the
> `Environment=SDL_VIDEODRIVER=kmsdrm` line and start the script from your
> desktop session's autostart instead of systemd. KMSDRM is just the cleanest
> path for a headless projector.

---

## Remote control (Home Assistant)

The app can render on start, or boot **idle** (black) and wait to be turned on.
Either way it always listens on `command_topic` (default
`halloween/eyes/command`), so Home Assistant can start/stop the whole pair with
one message — both Pis subscribe.

Set the boot behaviour per Pi in `config.ini`:
```ini
[control]
start = idle        ; or "playing"
```
For an HA-scheduled decoration, `idle` is usually right: the Pis power on dark
and wait for HA. (`--start playing|idle` overrides it on the command line.)

Commands are case-insensitive. **On:** `on` / `1` / `true` / `play` / `start`.
**Off:** `off` / `0` / `false` / `stop` / `pause`. A JSON `{"state": "ON"}` also
works (so an MQTT light/switch entity drives it directly).

**Publish the command retained** so a Pi that reboots mid-event immediately
picks up the current on/off state instead of waiting for the next automation.

Example HA automations (Settings → Automations, edit in YAML):
```yaml
- alias: "Halloween eyes ON at dusk"
  trigger: { platform: sun, event: sunset, offset: "-00:15:00" }
  action:
    - service: mqtt.publish
      data: { topic: "halloween/eyes/command", payload: "on", retain: true }

- alias: "Halloween eyes OFF at 11pm"
  trigger: { platform: time, at: "23:00:00" }
  action:
    - service: mqtt.publish
      data: { topic: "halloween/eyes/command", payload: "off", retain: true }
```
Prefer a switch you can also flip by hand? Add an `mqtt` switch pointing at the
same topic and have the automations toggle that instead.

---

## Following people/cars (target tracking)

The eye can track a real target. Publish a normalized position on `target_topic`
(default `halloween/eyes/target`) and the eye **snaps to it, follows it, and
narrows its pupil** in a focused stare; when updates stop for `target_hold`
seconds it drifts back to idle wandering. Only the **brain** subscribes — the
tracking rides the normal state stream, so both eyes track together for free.

The contract (your sensor bridge does the sensor-specific mapping):
- Coordinates are **normalized**: `x` from −1 (full left) to +1 (full right),
  `y` from −1 (down) to +1 (up). Publish as fast as your sensor updates.
- Look here: `{"x": 0.6, "y": -0.1}` — optionally `"hold"` to override the
  timeout for this target. A list `[{"x":..,"y":..}, ...]` uses the first entry,
  so have your bridge pick the nearest/strongest target.
- Target gone: `{"active": false}`, `{"state":"clear"}`, or the word `clear`
  (otherwise it just times out on its own after `target_hold`).

Where the coordinates come from is up to the bridge — a camera with person/car
detection, or an mmWave presence sensor. It maps the detection to a position
across the eye's field of view and publishes it. For a person walking left→right
past the house, that's `x` sweeping −1→+1.

Quick test by hand (watch the eye whip over and follow):
```bash
mosquitto_pub -t halloween/eyes/target -m '{"x":-0.9,"y":0}'
mosquitto_pub -t halloween/eyes/target -m '{"x":0.9,"y":0}'
mosquitto_pub -t halloween/eyes/target -m 'clear'
```

### Testing tracking without a sensor (`track_test.py`)

`track_test.py` simulates targets so you can exercise the whole tracking path
before the mmWave sensor is mounted. It reads the broker settings from
`config.ini`.

```bash
python track_test.py sweep          # a target walks back and forth across the view
python track_test.py approach       # walks up to the house, pauses, leaves
python track_test.py point -0.8 0.2 # hold one spot (Ctrl-C releases)
python track_test.py interactive    # drive it from the keyboard (a/d/w/s, 0-9, c)
python track_test.py monitor        # watch state/target/command traffic live
python track_test.py clear          # release any target
```

What you should see: the eyes snap to the target with a **surprise flare**,
settle into a narrowed **glare**, follow it across, and drift back to idle a
couple of seconds after it's cleared. `monitor` prints the live gaze/blink/
dilation/open values, which is the quickest way to confirm the brain is
reacting even without looking at the projector.

Tuning (top of `demoneye.py`): `TRACK_SNAP` (how fast it whips over),
`TRACK_FOLLOW` (follow responsiveness), `ALERT_DILATION` (pupil while locked on),
`target_hold` (in `config.ini`).

**Eyes mounted high?** They sit above the people they're watching, so a target
reported as level really needs a downward look. `track_look_down` in
`config.ini` `[control]` tilts the gaze down while tracking (0 = none, 0.35
default, 1.0 = as far down as the eye goes). It applies only while tracking —
idle wandering stays centred. If the sensor itself reports usable height, the
bias adds to it rather than replacing it.

**How it reacts to being noticed** is randomized so it never feels canned —
`REACT_WEIGHTS` in `demoneye.py` sets the mix:

| Style | Behaviour |
|---|---|
| `startle` | flares wide in surprise, then settles into the locked glare |
| `anger` | snaps straight to a narrowed, hostile glare — no flare |
| `slow` | barely reacts for a beat, then slowly hardens into the glare |

Defaults are 50 / 30 / 20. All three resolve into the same locked-on stare while
following, and relax to neutral once the target is gone. Weights don't need to
sum to 1 — set one to 0 to disable that style.

### Everything Presence Pro bridge (`ha_bridge.py`)

`ha_bridge.py` turns an Everything Presence Pro's mmWave tracking into those
target messages. It connects to Home Assistant over its WebSocket API, watches
the EP Pro's per-target X/Y coordinates (LD2450, up to 3 targets), picks the
**nearest active** one, normalizes and smooths it, and publishes to
`target_topic`. Run it anywhere that can reach HA and the broker — the brain Pi
is fine.

Setup:
1. Deps: `./venv/bin/pip install -r requirements-bridge.txt`
2. In HA, make a **Long-Lived Access Token** (your profile → Security). Put it
   in the `HA_TOKEN` environment variable (or `bridge.ini`, less secure).
3. In HA → Developer Tools → States, search `target` and copy your device's
   exact entity names into `bridge.ini` (`[entities]`). The default assumes a
   device named `presence_pro`; yours will differ.
4. Calibrate `x_range_mm`: watch a target's X value in HA as you stand at the
   left and right edges of the area you want the eye to sweep, and set it to
   that magnitude. Flip `invert_x` if the eye looks the wrong way.
5. Run it: `HA_TOKEN=... ./venv/bin/python ha_bridge.py` — walk across the
   field of view and the eye should follow. Then install `ha-bridge.service`
   (same pattern as `demoneye.service`) to autostart it.

**The bridge follows the same on/off command as the eyes.** There's no point
streaming radar data all day, so by default it publishes targets only while the
show is running — one Home Assistant message controls the eyes, the projectors
and the bridge together. In `bridge.ini`:

```ini
command_topic = halloween/eyes/command
follow_command = true     ; false = always publish
start_enabled = false     ; quiet until a command arrives
```

Keep your command messages **retained** so the bridge (and the Pis) pick up the
current state within a second of starting, rather than waiting for the next
transition. When it's told to stop, it publishes a `clear` first so the eyes
don't keep staring at a target that will never update again.

The EP Pro's LD2450 reaches ~6m with a fan-shaped, top-down field of view, so
it's ideal for people **approaching** the house (path, porch, driveway) rather
than fast traffic out on the street. Mount it facing the approach with its
lateral axis aligned to the eye's left/right.

---

## Powering the projectors (IR)

The Pis can power the projectors on and off with the **same Home Assistant
command** that starts and stops the show, so one automation runs everything.

It shells out to `ir-ctl`, which hands NEC encoding and the 38kHz carrier to the
kernel's IR driver — much more timing-reliable than bit-banging GPIO from
Python. You need a working IR transmitter and an `ir-ctl` keymap (the `.toml`
mapping scancodes to key names).

Test the hardware first, independent of the eyes:
```bash
python ir_control.py on
python ir_control.py off
python ir_control.py test        # on, wait 8s, off
python ir_control.py send KEY_POWER
```

Then enable it per Pi in `config.ini`:
```ini
[projector]
ir_enabled = true
keymap = /home/fpp/groview.toml
power_key = KEY_POWER
repeat = 1          ; send the code N times if the projector misses presses
off_confirm = 1     ; set 1 if POWER must be pressed twice to confirm shutdown
off_confirm_gap_s = 2.0   ; seconds between the press and the confirming press
warmup_s = 0        ; wait this long after power-on before the eye appears
```

Notes:
- If your remote has **discrete** on and off codes (better — no toggle drift),
  set `separate_on_off = true` and name `on_key` / `off_key`.
- IR is sent on a worker thread, so a slow send or warm-up never stalls
  rendering.
- Repeated identical commands don't re-fire, so a retained "on" message
  redelivered by the broker won't toggle a running projector off.
- With `start = idle`, a Pi boots dark and powers the projector only when HA
  says on; the projector is also powered on at startup if it boots into
  `playing`.

---

## Checking performance (`--stats`)

Add `--stats` to print a live line of FPS, CPU, GPU and temperature — the
quickest way to confirm a Pi is holding framerate with the heavier shaders
(the walker's animated fire is the most expensive).

```bash
./venv/bin/python demoneye.py --role brain --solo --eye walker --stats
```
```
fps  60.0  min  59.4  worst  18.2ms  cpu  22.0%  gpu  38.5%  52.1C  [glare]
```

- **min** is the lowest one-second average since start, and **worst** the single
  longest frame — both stick, so you can leave it running and check later
  whether anything stuttered.
- The trailing tag is the brain's current mood, handy while testing tracking.
- **gpu** comes from the V3D debugfs counter on Pi 4/5 and reports the busiest
  queue (RENDER for shader work). If that isn't readable it falls back to the
  v3d clock via `vcgencmd`, and failing that shows `n/a` rather than a
  misleading number. Reading the counter may need root — run with `sudo` if you
  see `n/a` on a Pi.
- `pip install psutil` gives more accurate CPU numbers; without it the value
  comes from `/proc/stat`.

If FPS sags below ~55 on the Pi, the first things to trim are the walker's fire
(the two `fbm` layers in its flame block) and the `fbm` octave counts (5 -> 4).

---

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

## If sync ever looks off

It shouldn't — gaze is drift-proof by design. The one timing-sensitive moment is
the blink (~200 ms). Across a house with the projectors far apart it won't be
noticeable, but if you ever want it frame-perfect, the upgrade is: sync both Pis'
clocks (they already run NTP) and have the brain schedule blinks at a shared
future timestamp instead of streaming them. Say the word and I'll add it.

## Fallback: one Pi, two projectors

If the networked sync ever gives you trouble, a single Pi 5 has two HDMI outs and
can drive both projectors from one frame-locked process — perfect sync, zero
networking. The catch is cabling both projectors back to one spot, which you
noted is a problem here. It's the safety net, not the plan.
