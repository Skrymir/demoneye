# Deploying to a Pi

## Files

| File | Goes on | Purpose |
|---|---|---|
| `demoneye.py` | both Pis | the eye app |
| `eye_shaders.py` | both Pis | the three eye shaders (demon / human / walker) |
| `ir_control.py` | Pis with an IR transmitter | projector power via `ir-ctl` |
| `stats.py` | both Pis | `--stats` performance readout |
| `config.ini` | both Pis | **per-Pi** settings — differs between them |
| `requirements.txt` | both Pis | python deps |
| `demoneye.service` | both Pis | autostart on boot |
| `ha_bridge.py`, `bridge.ini`, `requirements-bridge.txt`, `ha-bridge.service` | ONE machine only | mmWave tracking bridge |
| `track_test.py` | anywhere | simulate targets for testing |
| `nodered_flow.json` | Home Assistant | Node-RED MQTT output nodes |

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
center_x = 0.50        ; aim THIS projector
center_y = 0.50
scale    = 1.00
mirror   = true        ; on ONE Pi only, so the pair is symmetric
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
