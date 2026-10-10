# demoneye — synchronized projector eyes for Halloween

Two Raspberry Pi 5s, two projectors, one MQTT broker: a pair of animated
**eyes** projected onto the house that blink, glare and *follow people* via an
mmWave sensor.

Each eye is a GLSL shader drawn by a small Python app. One Pi (the **brain**)
runs the behaviour and streams the eye state once per frame (60 Hz); the other
(the **follower**) draws the same state with the same smoothing, so the pair
stays in step.

## Quick start (on your Mac)

```bash
python3.13 -m venv venv && ./venv/bin/pip install -r requirements-dev.txt -c constraints.txt
./venv/bin/python demoneye.py --role brain --solo --windowed
./venv/bin/python demoneye.py --role brain --solo --windowed --eye demon
```

`--solo` needs no broker. Add `--stats` for a live FPS/CPU/GPU readout. Press
Esc or Q to quit.

## The eyes

| `--eye` | what it is |
|---|---|
| `walker` (default) | frozen undead: luminous crystalline ice-blue iris with cold blue fire |
| `demon` | dragon: full-aperture amber cellular iris, ragged slit pupil, fiery glow |
| `human` | bloodshot eyeball in fleshy lids; wide horror stare and hooded glare |

All three share one emotion system (surprise, anger, glare) and react to being
noticed. Use the same `--eye` on both Pis.

## Docs

- **[DEPLOY.md](DEPLOY.md)** — installing on the Pis, autostart, the matched-pair checklist
- **[docs/EYES.md](docs/EYES.md)** — the eye types, tuning their look, mirroring a pair
- **[docs/TRACKING.md](docs/TRACKING.md)** — the mmWave bridge, calibration, `track_test.py`, projector IR
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — running on the projectors, on/off behaviour, `--stats`, frame rate, sync troubleshooting
- **[homeassistant/](homeassistant/README.md)** — everything on the Home Assistant side: live show status, the dashboard card, alerts, the Node-RED on/off flow

## Layout

```
demoneye.py      the app: window, MQTT, main loop, follower watchdog
settings.py      every tunable + config.ini loading
renderers.py     the eye renderer (state schema, director, uniforms)
directors.py     the eye behaviour (saccades, blinks, moods, tracking)
eye_shaders.py   shader bodies for the three eyes
glsl_common.py   the shared GLSL prelude (noise helpers) + assembler
glutil.py        compile/link, fullscreen triangle
ha_bridge.py     Home Assistant -> MQTT target bridge      (bridge.ini)
ha_status.py     show status for Home Assistant (MQTT discovery), used by both
ir_control.py    projector power over IR via ir-ctl
stats.py         --stats readout
check_config.py  verify both Pis' settings from the Mac   deploy.sh  ship to both Pis
sync_probe.py    measure eye-sync latency on a Pi          track_check.py  diagnose tracking
track_test.py    simulate targets       tests/           pytest suite
test_pattern.py  projector alignment pattern (run with test_pattern.sh on a Pi)
homeassistant/   HA dashboard card, automations, Node-RED flow (not deployed to the Pis)
```

Adding an eye is one shader body in `eye_shaders.py`.

## Tests

```bash
./venv/bin/pip install -r requirements-dev.txt -c constraints.txt
./venv/bin/python -m pytest -q tests
```

GitHub Actions runs the same suite on every push and pull request
(`.github/workflows/tests.yml`), on Linux with Mesa's EGL so the
draw-a-frame tests run too. There, any skipped test fails the run.

Every eye shader is compiled for both the Pi's GLSL ES 3.00 and desktop 3.30
(that catches reserved-name bugs the Pi tolerates and the Mac doesn't), the
director is checked against the renderer's state schema, the config
round-trips, and every eye draws a frame offscreen.

## Config

Per-machine settings live in `config.ini` next to the script and are never
touched by code updates: broker, eye placement (`center_x/y`, `scale`,
`mirror`, `gaze_scale`), HDMI output mode, tracking tilt, projector IR, and the
follower watchdog (`stale_after_s`). Defaults and documentation for every key
are in `settings.py`.

### Credentials

`config.ini` and `bridge.ini` in this repo are templates: leave `username`,
`password` and `token` blank here. On each Pi, put real values in root-only
env files that the services load (`/etc/demoneye.env`: `MQTT_USER`,
`MQTT_PASS`; `/etc/ha-bridge.env`: `HA_TOKEN`, plus `MQTT_USER`/`MQTT_PASS`
if your broker needs a login); see the comments in
`demoneye.service` and `ha-bridge.service`. `check_config.py` warns if a token
is sitting in `bridge.ini`.

## License

MIT, see [LICENSE](LICENSE).
