# Projector power without the eyes (`projector_service.py`)

For seasons when the Pis are booted into FPP (Christmas: the Pis are FPP
remotes in fpp-picap1's MultiSync and FPP plays the window videos). It's the
projector half of demoneye on its own, set up the same way as Halloween: it
follows one retained show command and confirms power by HDMI (`ir_control.py`:
no press if HDMI already shows the wanted state, one retry if a press was
missed).

It is only `ir_control.py` + `ha_status.py` behind MQTT. No pygame, no GL, no
display, so it doesn't compete with FPP for HDMI.

## MQTT

| Topic | Direction | Payload |
|---|---|---|
| `house/christmas/command` | Node-RED / `switch.christmas_show` → both Pis, **retained**, QoS 1 | `on` / `off` (same words demoneye accepts, or `{"state":"ON"}`); anything else, including `finish`, is ignored |
| `house/status/projector_<node>/state` | Pi → HA, retained | `{"power": "on\|off\|unknown", "result": "...", "desired": "on\|off\|none", "changed": ISO, ...}` |
| `house/status/projector_<node>/availability` | Pi → HA, retained, LWT | `online` / `offline` |

Like `house/show/command` for Halloween, the command is the show's state: a Pi
that reboots mid-evening picks it up and gets its projector there. A replay of
the command already carried out (every MQTT reconnect replays the retained
one) is ignored unless the last attempt failed, so a projector whose HDMI state
can't be read isn't toggled by a network blip.

## Scheduled stop

The 10 PM stop doesn't cut the show off mid-sequence. Node-RED publishes
`finish` on `house/christmas/command`, and the Christmas Show tab tells FPP to stop
gracefully. This service ignores `finish`, as it ignores any payload that isn't
an on or off word (`directors._parse_command` returns `None` for it), so the
projectors stay on while FPP finishes the sequence. Once FPP is idle, the
Christmas Show tab publishes the retained `off` and the projectors turn off. A
manual stop publishes `off` straight away, and everything stops at once.

Don't add `finish` to the on/off words: the projectors would go dark while the
lights are still playing. `tests/test_projector_service.py` pins this down. The
full design (the Holiday Shows dispatcher, the FPP bridge, the dashboard and
alerts) is in the
[christmas-show repo's `homeassistant/`](https://github.com/Skrymir/christmas-show/tree/master/homeassistant).

## Status

`result` goes `turning_on` → `on`, or `turning_off` → `off`. A press the
projector never answered (after `retries`) ends in `failed_on` / `failed_off`.
`power` is the live HDMI state, polled every `projector_poll_s`.

Home Assistant sees it as demoneye's own **`binary_sensor.projector_left`** /
**`_right`**, on the **Demon Eye Left/Right** device: same unique_id and entity
id, but with this service's state and availability topics, and `result`,
`desired` and `changed` as attributes. Dashboards and alerts that use those
entities work in both seasons. The eye's other entities just show unavailable
for the season, and when the Pi boots back into demoneye, the eye re-publishes the
entity's discovery and takes it back.

## Install on an FPP-booted Pi

The keymap path (`/home/fpp/groview.toml`) dates from when IR first ran under
FPP, so the IR overlay and keymap may already be on the FPP card. Check before
adding anything:

```bash
ssh fpp@pi-eye-left.home
ls -l /dev/lirc* ~/groview.toml          # transmitter device + keymap present?
grep -n gpio-ir-tx /boot/firmware/config.txt
which ir-ctl || sudo apt install -y v4l-utils
```

If `gpio-ir-tx` is missing, add the same `dtoverlay=gpio-ir-tx,gpio_pin=N`
line the Pi OS card uses (copy it from that card's `/boot/firmware/config.txt`),
then reboot and confirm it **survives a second reboot**, since FPP manages parts
of that file.

**HDMI feedback.** Verification depends on a powered-off projector reading
`disconnected`:

```bash
cat /sys/class/drm/card*-HDMI-A-*/status
cat /proc/cmdline | tr ' ' '\n' | grep video=
```

If FPP's display settings force HDMI on (`video=HDMI-A-1:...D` on the kernel
command line), the status always reads `connected` and `ir_control` falls back
to blind toggles and logs a warning. In that case, turn the forced mode off in FPP.
`hdmi_force_hotplug` in config.txt can hide the state the same way.

Copy the code and set up the venv (from the Mac, in this repo):

```bash
rsync -av --exclude venv --exclude .git --exclude tests --exclude homeassistant \
  --exclude 'config.ini' ./ fpp@pi-eye-left.home:demoneye/
ssh fpp@pi-eye-left.home
cd ~/demoneye
python3 -m venv venv && ./venv/bin/pip install 'paho-mqtt>=2,<3'
#  (no python3-venv on the image? sudo apt install -y python3-paho-mqtt and
#   point ExecStart at /usr/bin/python3; the service works with paho 1.6 too)
```

Create `~/demoneye/config.ini` with `vi`. Copy the `[mqtt]` and `[projector]`
sections from the Pi OS card's config.ini (`ir_enabled` is ignored here), and
add:

```ini
[projector_service]
# auto = left / right from the hostname
node = auto
```

Test by hand, then install the service:

```bash
./venv/bin/python ir_control.py status
./venv/bin/python ir_control.py test       # on, wait, off, both confirmed by HDMI
sudo cp projector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now projector
journalctl -u projector -f
```

From anywhere on the LAN (this is the real show switch, so it's retained):

```bash
mosquitto_sub -h 192.168.86.5 -t 'house/status/projector_+/state' -v &
mosquitto_pub -h 192.168.86.5 -t house/christmas/command -r -q 1 -m on
mosquitto_pub -h 192.168.86.5 -t house/christmas/command -r -q 1 -m off
```

## Check with FPP

FPP remotes play video out of the same HDMI port. Test once that a remote
which booted with its projector **off** still plays video after this service
turns the projector on and the master starts a playlist. If it doesn't, FPP is
deciding about the display at boot, and the show flow needs to restart fppd on
the remotes after power-on, or power the projectors on earlier.
