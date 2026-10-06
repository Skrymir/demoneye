# Deploying to a Pi

## Files

| File | Goes on | Purpose |
|---|---|---|
| `demoneye.py` | both Pis | the app (window, MQTT, loop, watchdog) |
| `settings.py` `renderers.py` `directors.py` `stage.py` | both Pis | behaviour + rendering |
| `eye_shaders.py` `stage_shaders.py` `glsl_common.py` `glutil.py` | both Pis | the shaders |
| `stats.py` | both Pis | `--stats` readout |
| `ir_control.py` | Pis with an IR transmitter | projector power via `ir-ctl` |
| `config.ini` | both Pis | **per-Pi** settings — differs between them |
| `requirements.txt` | both Pis | python deps |
| `demoneye.service` | both Pis | autostart on boot |
| `figures/` | both Pis (if using sprites) | sprite atlases |
| `ha_bridge.py` `bridge.ini` `requirements-bridge.txt` `ha-bridge.service` | ONE machine only | mmWave tracking bridge |
| `sprite_atlas.py` `track_test.py` `tests/` | anywhere | tools + tests |
| `nodered_flow.json` | Home Assistant | Node-RED MQTT output nodes |

The simplest deployment is `git pull` on each Pi — every file above is in the
repo except each Pi's own `config.ini` values (see `.gitignore`).

## Check both Pis' settings: `check_config.py`

```bash
python3 check_config.py           # both Pis, read-only (eyes + tracking bridge)
```

Checks each `config.ini` (unknown keys and typos, types and ranges, inline
comments — which the app does **not** strip, so `mirror = true ; note` breaks
startup), that the broker is reachable from each Pi, the IR setup, the
installed service's role/mode/eye, and that `SDL_KMSDRM_DEVICE_INDEX` matches
the HDMI card the projector is on. Then the pair: exactly one brain, same
show, same broker and topics, `mirror` on one Pi at most, and non-overlapping
stage spans. Exits non-zero on any error. `--file a.ini b.ini` checks local
copies without ssh (a `bridge.ini` is recognised and checked against the
bridge's own keys).

It also reports the **tracking bridge** on each Pi: whether `ha-bridge.service`
is installed, enabled and running (with its last log line if it's failing),
the `bridge.ini` keys, that there's an HA token (`/etc/ha-bridge.env`), its
Python deps, that Home Assistant is reachable, and that its broker and topics
match that Pi's `config.ini`. Across the pair it errors if the bridge runs on
**both** Pis and warns if it runs on neither.

## Updating both Pis: `./deploy.sh`

From your Mac, in the project folder:

```bash
./deploy.sh                 # test, copy to both Pis, install deps, restart, show logs
./deploy.sh pi-eye-right.home    # just one Pi
./deploy.sh --no-restart    # copy only
```

It runs the test suite first and refuses to ship if anything fails. Each Pi's
own `config.ini` / `bridge.ini` and its `venv` are never copied over or
deleted, so per-Pi placement, `mirror` and stage spans survive every deploy.
Only changed files are sent. On the Pi that runs the tracking bridge, it
restarts `ha-bridge` too so the new code takes effect.

One-time setup (so it doesn't prompt for passwords):
```bash
ssh-copy-id joshc@pi-eye-left.home
ssh-copy-id joshc@pi-eye-right.home
```
and on each Pi, allow the service restarts without a password:
```bash
echo 'joshc ALL=(root) NOPASSWD: /usr/bin/systemctl restart demoneye, /usr/bin/systemctl restart ha-bridge' \
  | sudo tee /etc/sudoers.d/demoneye && sudo chmod 440 /etc/sudoers.d/demoneye
```
Edit `PIS=(...)` at the top of `deploy.sh` if your hostnames differ.

## Second Pi, from scratch

```bash
# 1. OS packages (Pi OS Lite)
sudo apt update
sudo apt install -y python3-venv python3-dev \
    libgles2 libegl1 libgbm1 libgl1-mesa-dri
sudo usermod -aG video,render $USER        # log out/in after this
# only if this Pi drives a projector over IR:
sudo apt install -y v4l-utils

# 2. Files + venv
mkdir -p ~/demoneye && cd ~/demoneye       # copy the files here
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 3. Smoke test (no MQTT, straight to HDMI)
EYE_GL=es ./venv/bin/python demoneye.py --role brain --solo --eye walker --stats
```

You should get `[gl] OpenGL ES ... (GLES)` and an eye on the projector.

## What to change on the SECOND Pi

`config.ini` is per-machine. Same `[mqtt]` section as Pi #1, but:

```ini
[display]
; aim THIS projector
center_x = 0.50
center_y = 0.50
scale    = 1.00
; on ONE Pi only, so the pair is symmetric
mirror   = true
```

And in the service file, `--role follower` (exactly one Pi is `brain`).
Use the **same** `--eye` on both.

## Autostart

```bash
# edit demoneye.service first: --role, --eye, User=, paths
sudo cp demoneye.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now demoneye
journalctl -u demoneye -f
```

Also: boot to console (`sudo raspi-config` → System Options → Boot/Auto Login →
Console), and add `consoleblank=0` to the end of the line in
`/boot/firmware/cmdline.txt` so the screen never blanks.

## Checklist for a matched pair

- [ ] exactly one Pi is `--role brain`
- [ ] same `--eye` on both
- [ ] same `[mqtt]` host/topics on both
- [ ] `mirror = true` on one Pi only
- [ ] `gaze_scale` / `track_look_down` match (unless the projectors differ in height)
- [ ] `ir_enabled` set only on Pis that actually have a transmitter
- [ ] the tracking bridge runs on ONE machine, not both
