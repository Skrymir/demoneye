# Changelog

## Project-neutral MQTT topics — 2026-10-10

The topics are shared with the Christmas/Godot windows now, so they no longer
say Halloween or eyes where they aren't eye-specific:

| Was | Now |
|---|---|
| `halloween/eyes/command` | `house/show/command` |
| `halloween/eyes/target` | `house/tracking/target` |
| `halloween/eyes/status/<node>/…` | `house/status/<node>/…` |
| `halloween/eyes/state` | `house/eyes/state` |

Code defaults, the `config.ini` / `bridge.ini` templates, the Node-RED flow and
the docs are updated; `tests/test_topics.py` keeps every copy in agreement.
Home Assistant entities keep their IDs (discovery is re-published with the new
topics). **Node-RED: change the two `halloween/eyes/command` nodes to
`house/show/command`** (or re-import the flow).

## Code review fixes — 2026-10-10

Reliability (PRs #1, #4):
- **Eyes and bridge retry the broker** in the background instead of giving up
  if it wasn't up at boot (`connect_async`). A refused connection (bad
  credentials) is logged and no longer reported as "online".
- **Thread races fixed:** a target cleared mid-frame could crash the brain;
  an "off" landing mid-update could leave a target out after the clear.
- **Bridge clears the target when Home Assistant drops**, instead of
  re-sending the last point for the whole outage.
- **Monotonic clocks** for the IR power wait, follower watchdog and target
  deadlines: NTP stepping the clock after boot could toggle a booting
  projector back off.
- **NaN/Infinity on MQTT** is rejected (it used to stick in the follower until
  restart); a target `hold` is capped at 30 s. `scale` <= 0 in `config.ini`
  falls back to 1.0.
- **Status thread** survives publish errors and retries a failed snapshot.
- **Ctrl-C** exits cleanly like `systemctl stop`; a second Ctrl-C force-quits.

Deploying (PRs #1, #4):
- `deploy.sh`: one Pi failing `pip install` no longer aborts the rest; installs
  the bridge's requirements; won't ship without pytest or with tests skipped
  for a missing dependency; keeps `*.bak`, `config.ini.*` and `local/` on the
  Pis and never ships `.env` / `secrets/`.
- **Pinned dependencies:** ranges below the next major in `requirements*.txt`,
  exact versions in the new `constraints.txt`, which CI and `deploy.sh` install
  from. `requirements-dev.txt` for the Mac and CI.
- Credentials: `/etc/demoneye.env` is loaded by the unit; the bridge reads
  `MQTT_USER`/`MQTT_PASS` from `/etc/ha-bridge.env`.

Tools and docs:
- **CI** (PR #2): GitHub Actions runs the suite on Linux with Mesa EGL, so the
  GL draw tests run too; any skip fails the run.
- **Eye logs** (PR #3): `journalctl -t demoneye -f`. `-u demoneye` never showed
  the app's output (logind moves the process into a session scope).
- `check_config.py`: understands `--role=brain`; a `[mqtt]` key left out
  equals the same key set to its default.
- `test_pattern.sh`: uses the installed unit's KMSDRM index, restarts the eye
  with the password-free `restart`, reports a failed pattern.
- `track_check.py`: `--since` takes `yesterday`, `-2h`, dates with seconds;
  a failed `--simulate` post no longer crashes it.
- Docs: simple-mode units for `y_near_mm`/`y_far_mm`, 60 Hz sync spacing,
  and other corrections. Status `version` is now `2026.10.10`.

On the Pis: add `SyslogIdentifier=demoneye` to the installed unit (done on both).

## Projector alignment pattern — 2026-10-10

- **`test_pattern.sh` / `test_pattern.py`**: a full-screen alignment pattern
  (border, corner circles, % ruler, grid, LEFT/RIGHT label from the hostname)
  for matching the two projectors' size, height and keystone. It also draws
  the walker eye's outline where `config.ini` `[display]` puts it and follows
  edits to that file live. The wrapper stops the eye service and restarts it
  on exit. Docs: `docs/OPERATIONS.md` "Lining up the two projectors".
  Tests: `tests/test_alignment_pattern.py`. No config changes.

## Govee lights — 2026-10-07

- **The Govee lights follow the show.** `homeassistant/nodered_flow.json`
  now has a second chain: it listens to `halloween/eyes/command`, turns on
  `scene.govee_to_mqtt_one_click_leisure_blue_fire` when the show starts and
  `scene.govee_to_mqtt_one_click_leisure_night_mode` when it stops (Govee Tap-to-Runs
  via govee2mqtt). Same on/off words as the eyes;
  only changes trigger it, so a Node-RED restart doesn't repaint the lights.
  Setup is in `homeassistant/README.md` → "Govee lights". No change on the Pis.

## Eyes only — 2026-10-07

- **Stage mode removed.** The app now only draws the eyes; the stage figures
  (silhouette, sprite, ghost, mansion ballroom, spirits) live on in the Godot
  "haunted windows" project. Removed `stage.py`, `stage_shaders.py`,
  `sprite_atlas.py`, `figures/`, `docs/STAGE.md`, the `[stage]` config section
  and its settings, the stage-only GLSL helpers, the sprite-atlas loader and
  the Pillow dependency.
- `--mode` is gone; `--mode eyes` is still accepted so an older service line
  keeps working. `--eye` now only offers `walker` (default), `demon`, `human`.
- `check_config.py`: a leftover `[stage]` section in a Pi's own `config.ini` is
  reported as "safe to delete" (the app ignores it); a service still running
  `--mode stage` is an error. Pair check compares the eye only.
- Home Assistant: the role entity no longer has a `mode` attribute (dashboard
  card and README updated).
- `deploy.sh` no longer requires the stage files; its `rsync --delete` removes
  them from the Pis on the next deploy.
- The three eyes render pixel-identically before and after (verified).
- Removed `homeassistant/show_schedule.yaml`: Node-RED schedules the show; the
  HA README now describes only the Node-RED flow.
- Removed a stray old copy of `deploy.sh` from `tests/`.

## Walker eye — 2026-10-07

- **The walker is now the default eye** (`--eye walker`). Both services start
  without `--eye`, so a deploy switches the show to it.
- **Walker shader ~1.8x faster, identical picture.** Its frost, iris and fire
  layers (11 noise evaluations plus a voronoi per pixel) were computed for
  every pixel around the eye, including the eyelid skin where `lid` multiplies
  them to zero. They now run only inside the aperture, and the sclera and iris
  each only where the other isn't fully opaque. Verified pixel-identical over
  40 frames (gazes, blinks, dilation, moods, fire phases); 27.5 -> 15.6 ms per
  720p frame in software GL.

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
