# Changelog

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
