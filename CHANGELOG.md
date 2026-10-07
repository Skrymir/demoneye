# Changelog

## Eye sync latency — 2026-10-06

- **Frame pacing, measured on the Pi 5 (720p demon eye, 60 Hz):** frames
  capped just above the refresh rate = 66 fps, 0 late frames (kept as the
  default). Double-buffering (`SDL_VIDEO_DOUBLE_BUFFER=1`) = locked 30 fps.
  Sleeping to exactly 60 = 17 fps (it fights the driver's own page-flip wait).
  `--pacing cap|exact|off` for comparing.
- **Measured on the brain (720p output, vsync on):** `render_height` 0 = 66 fps,
  0 late frames; 540 = 50 fps, 14 late/s; 360 = 66 fps, 11 late/s. The extra
  scaling pass costs more than it saves on the Pi, so leave `render_height = 0`
  (the default). New `--render-height` / `--output-mode` flags for testing.
- **Vsync on; frame pacing fixed.** The display was opened without vsync and
  the loop capped itself with sleeps that weren't aligned to the projector's
  refresh, so frames landed out of step (some shown twice, some never): judder
  even at 55 fps. Now each flip waits for the refresh, and the cap is only a
  safety net. The log shows `@ 60 Hz, vsync on`.
- **`--stats` is honest about smoothness.** New `late` (frames that missed a
  refresh, per second) and `p95` frame time. The `vcgencmd` GPU-clock read
  moved off the render thread; it was causing a 20-60 ms hitch every second
  in --stats mode (the "worst 68 ms").
- **Demon eye renders ~2x faster, same picture.** Its iris texture (2x
  voronoi + fbm) was computed for every pixel on screen and then multiplied by
  zero outside the lids; it's now only computed inside them. Pixel-identical
  output (verified), about 1.8x faster. The brain was measured at 15 fps.
- **Pi outputs 1280x720 (`[display] output_mode`).** The app asks the Pi for a
  real 720p HDMI mode instead of the projector's native 1080p: ~2.25x fewer
  pixels for the shader, and the projector does the scaling. No `/boot`
  changes, and HDMI hot-plug (the IR power check) still works. `native` = old
  behaviour. Optional `render_height` draws below the output and scales up on
  the GPU. The log shows the screen size and render size.
- Removed the "waiting for display" tip that suggested forcing the HDMI mode
  (that breaks the IR power verification).
- **Brain now streams 60 msg/s, not 30.** It published when 20 ms (50 Hz) had
  passed, but 60 fps frames are 16.7 ms apart, so the check only passed every
  other frame: 30 msg/s. It now publishes on a frame count (every frame at
  60 fps), giving the follower twice the updates. `PUBLISH_HZ` default is 60.
- **Nagle off on MQTT sockets.** paho leaves Nagle's algorithm on, which can
  hold small 50 Hz state messages back and release them in clumps. The eyes
  and the bridge now set `TCP_NODELAY` on every (re)connect. The broker needs
  `set_tcp_nodelay true` too: on a test broker that took p95 message spacing
  from 58 ms to 21 ms.
- New `sync_probe.py`, run on a Pi during the show: measures the state stream's
  spacing and bunching, MQTT round trip, ping and Wi-Fi power save/signal, and
  names the likely cause.
- `check_config.py` warns when the broker is reached over Wi-Fi with power
  saving on, or with a weak signal.
- docs/OPERATIONS.md: "Eyes out of sync" troubleshooting (Wi-Fi power save,
  Mosquitto add-on `set_tcp_nodelay`, projector game mode/keystone).

## Show status in Home Assistant — 2026-10-06

- **New `ha_status.py`: live status over MQTT discovery.** Both eyes and the
  tracking bridge publish retained, low-rate status plus HA discovery configs,
  so three devices appear in Home Assistant with nothing to install: per eye
  *Running* (MQTT last will), *Projector* (HDMI state), *Show*
  (`playing` / `idle` / `waiting_for_display` / `lost_brain`) and *Role*
  (with mode, eye, version, host). The bridge reports *Running*, *Tracking*,
  *Last target seen* (timestamp, survives restarts), *Host* and its *Home
  Assistant link*. The brain also publishes a *Show* switch on the command
  topic for manual on/off. Published on change plus a 30/60 s refresh, never
  at frame rate; resent when HA restarts. Clean stops publish `offline` at
  once; the bridge now handles SIGTERM cleanly too.
- New optional `[status]` section in `config.ini` and `bridge.ini` (all keys
  default sensibly, so existing Pi configs need no change). `check_config.py`
  knows the keys and errors if both Pis would publish under the same node name.
- New top-level `homeassistant/` folder for everything on the HA side: its
  README (status entities, setup, topics), a paste-in dashboard card, four
  alert automations, the dusk / 11 pm schedule (moved out of
  `docs/OPERATIONS.md`) and `nodered_flow.json` (moved from the top level).
  `deploy.sh` doesn't copy it to the Pis. 31 new tests (`tests/test_status.py`),
  and verified end to end against a real Mosquitto broker: discovery, state
  changes, clean stop, and the last will after a `kill -9`.

## Tracking bridge on/off and checks — 2026-10-06

- **Bridge goes fully quiet when the show is off.** It already published
  nothing to MQTT while off; now it also drops its Home Assistant WebSocket
  (which receives every state change in the house) and reconnects on *on*.
  3 new tests (`tests/test_bridge.py`).
- `check_config.py` reports the tracking bridge on each Pi (installed /
  enabled / running, last log line when failing, `bridge.ini` keys, HA token,
  deps, HA reachable, broker/topics match `config.ini`) and errors if it runs
  on both Pis. `--file bridge.ini` works.
- `deploy.sh` restarts `ha-bridge` on the Pi that runs it. The sudoers line
  now covers both restarts.

## Display hardening — 2026-10-06

- **Projector-off deadlock fixed.** With the projector off, its HDMI port
  reads as disconnected and the app used to exit at startup — before
  connecting to MQTT, so it could never hear the *on* command that powers the
  projector over IR (systemd just restarted it forever). Now MQTT starts
  first, and the app **waits for a display** (`display_retry_s`, default 5 s)
  instead of exiting. Verified: booted with the projector off, received *on*,
  sent IR power-on, picked up the display when the projector came up.
  `systemctl stop` during the wait still exits in well under a second;
  `--windowed` desktop runs still fail fast.
- **Closed-loop projector power.** IR commands are verified against the HDMI
  hot-plug state (an off projector reads *disconnected*): no press if the
  projector is already in the wanted state — so a toggle POWER button can't
  be flipped the wrong way by a restarted Pi or a missed shot — then wait for
  the change and retry once if it didn't happen. Concurrent on/off commands
  are serialised. New `[projector]` keys: `verify`, `status_path`,
  `on_timeout_s`, `off_timeout_s`, `retries`. `python ir_control.py status`.
  8 new tests against a simulated toggle projector.
- `check_config.py` reads each Pi's kernel command line: warns if a forced
  HDMI mode (`video=...D`) would blind IR verification, flags a `cmdline.txt`
  with more than one line (the kernel ignores everything after line 1), and
  treats "no projector on HDMI" as normal when it's switched off.
- `deploy.sh` (one-command deploy to both Pis) and `check_config.py` (config
  verifier) added; hostnames are `pi-eye-left.home` / `pi-eye-right.home`.
- Docs: every `key = value ; comment` example rewritten with the comment on
  its own line — the app doesn't strip inline comments.

## Cleanup release — 2026-09-16

Same show, a third less code, and a test suite. No visible behaviour change:
identical state rendered through the old and new code is **pixel-identical**
for 7 of 8 figure types; the spirits differ only because their local noise
used a lacunarity of 2.03 where every other shader used 2.02 and the shared
prelude standardises on 2.02 (imperceptible in animated noise).

### Structure
- `demoneye.py` 1,081 → ~300 lines. Drawing is delegated to a **Renderer**
  (`renderers.py`): one class per figure type owning its state schema,
  director, uniforms and draw. The 13 `if mode == "stage"` branches are gone;
  adding a figure is one class + one shader body.
- `settings.py` holds every tunable and `load_config()`; `directors.py` holds
  the eye Director; `glutil.py` the GL plumbing.
- `glsl_common.py`: **one shared GLSL prelude** (noise, SDFs, smin, room
  perspective) instead of the same helpers pasted into eight shaders.
  Constants such as the number of ballroom/spirit slots and trail length are
  injected as `#define`s from Python, so they can no longer drift.

### Robustness
- **Follower watchdog** (`stale_after_s`, default 2 s): a follower that stops
  hearing from the brain goes dark instead of freezing on its last frame all
  night, and recovers when state resumes.
- **Clean exit.** SDL/EGL teardown could hang on quit, turning `systemctl stop`
  into a 90 s wait and a SIGKILL (this was pre-existing). The app now
  disconnects MQTT cleanly and hard-exits; the kernel reclaims the display.
- Only the display subsystem is initialised — no more `ALSA lib ... cannot
  find card` spam in the journal on a Pi with no sound device.

### Tests (`python -m pytest -q tests`, ~2 s)
- every shader compiled for GLSL ES 3.00 **and** desktop 3.30 (this is what
  catches reserved-name bugs the Pi tolerates and the Mac rejects)
- every director emits exactly its renderer's schema, every frame
- stride keeps feet planted at any speed; ballroom dancers stagger
- config round-trip; every renderer draws a frame offscreen

### Docs & config
- README split into a quickstart plus `docs/EYES.md`, `STAGE.md`,
  `TRACKING.md`, `OPERATIONS.md`; `DEPLOY.md` file table updated.
- `bridge.ini` / `ha_bridge.py` default to the **S1 Pro Multi Sense** entity
  names and `x_range_mm = 250` (the shipped 3000 was 12× off for its units).
- Service files document the `EnvironmentFile=` pattern so tokens and
  passwords stay out of the repo; `ha-bridge.service` uses it.
- Removed the vestigial `crouch` state; `ir_control.configure()` replaces
  poking its globals.

### Deploying
Pull the branch on both Pis (or copy everything), then on each:
```bash
./venv/bin/pip install -r requirements.txt   # unchanged deps
sudo systemctl restart demoneye
```
`config.ini` gains one optional key, `stale_after_s` under `[control]`; the
default applies if it's absent.
