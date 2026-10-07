# demoneye — a synchronized projector show for Halloween

Two Raspberry Pi 5s, two projectors, one MQTT broker. Either a pair of animated
**eyes** that blink, glare and *follow people* via an mmWave sensor, or a
**stage**: the projectors become windows onto one virtual room that figures and
spirits move through — crossing from window to window, drifting into the
depth of the room, and freezing when someone looks.

Everything is a GLSL shader rendered by a tiny Python app; one Pi (the
**brain**) runs the behaviour and streams state at 50 Hz, the other (the
**follower**) renders the same state with the same smoothing, so they stay
matched to within a fraction of a percent.

## Quick start (on your Mac)

```bash
python3.13 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python demoneye.py --role brain --solo --windowed --eye walker
./venv/bin/python demoneye.py --role brain --solo --windowed --mode stage --eye spirits
```

`--solo` needs no broker. Add `--stats` for a live FPS/CPU/GPU readout. Press
Esc or Q to quit.

## What you can show

| `--mode` | `--eye` | what it is |
|---|---|---|
| `eyes` | `demon` | dragon: full-aperture amber cellular iris, ragged slit pupil, fiery glow |
| `eyes` | `human` | bloodshot eyeball in fleshy lids; wide horror stare and hooded glare |
| `eyes` | `walker` | frozen undead: luminous crystalline ice-blue iris with cold blue fire |
| `stage` | `silhouette` | a procedural walking figure, backlit in a lit window |
| `stage` | `sprite` | your own walk cycle (Blender/Mixamo/filmed) from a sprite atlas |
| `stage` | `ghost` | translucent hooded spectre drifting in 3D |
| `stage` | `mansion` | the ballroom: three Victorian ghosts waltzing at depth |
| `stage` | `spirits` | faceless orbs and wisps flying the room in 3D, with curved trails |

All eyes share one emotion system (surprise, anger, glare) and react to being
noticed. All stage figures share the room's depth model, and the mmWave bridge
makes them react to being watched.

## Docs

- **[DEPLOY.md](DEPLOY.md)** — installing on the Pis, autostart, the matched-pair checklist
- **[docs/EYES.md](docs/EYES.md)** — the eye types, tuning their look, mirroring a pair
- **[docs/STAGE.md](docs/STAGE.md)** — stage mode, window spans, the figure types, sprite atlases
- **[docs/TRACKING.md](docs/TRACKING.md)** — the mmWave bridge, calibration, `track_test.py`, projector IR
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — running on the projectors, on/off behaviour, `--stats`, troubleshooting
- **[homeassistant/](homeassistant/README.md)** — everything on the Home Assistant side: live show status, the dashboard card, alerts, the on/off schedule (HA or Node-RED)

## Layout

```
demoneye.py      the app: window, MQTT, main loop, follower watchdog
settings.py      every tunable + config.ini loading
renderers.py     one class per figure type (schema, director, uniforms, draw)
directors.py     the eye behaviour (saccades, blinks, moods, tracking)
stage.py         stage behaviours: walk, drift, ballroom, spirits
eye_shaders.py   shader bodies for the eyes
stage_shaders.py shader bodies for the stage figures
glsl_common.py   the shared GLSL prelude + assembler (injects #defines)
glutil.py        compile/link, fullscreen triangle, texture upload
ha_bridge.py     Home Assistant -> MQTT target bridge      (bridge.ini)
ha_status.py     show status for Home Assistant (MQTT discovery), used by both
ir_control.py    projector power over IR via ir-ctl
stats.py         --stats readout        sprite_atlas.py  pack PNG frames
track_test.py    simulate targets       tests/           pytest suite
homeassistant/   HA dashboard card, automations, Node-RED flow (not deployed to the Pis)
```

Adding a figure is one Renderer class and one shader body; the app never
branches on what it's drawing.

## Tests

```bash
./venv/bin/pip install pytest
./venv/bin/python -m pytest -q tests
```

Every shader is compiled for both the Pi's GLSL ES 3.00 and desktop 3.30
(that catches reserved-name bugs the Pi tolerates and the Mac doesn't), every
director is checked against its renderer's state schema, the config
round-trips, and every renderer draws a frame offscreen.

## Config

Per-machine settings live in `config.ini` next to the script and are never
touched by code updates: broker, eye placement (`center_x/y`, `scale`,
`mirror`, `gaze_scale`), the window's stage span (`stage_from/to`), tracking
tilt, projector IR, and the follower watchdog (`stale_after_s`). Defaults and
documentation for every key are in `settings.py`.
