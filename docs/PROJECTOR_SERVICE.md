# Projector power without the eyes (`projector_service.py`)

For seasons when the Pis are booted into FPP (Christmas: the Pis are FPP
remotes in fpp-picap1's MultiSync and FPP plays the window videos). Home
Assistant still owns projector power, using the same closed-loop IR control as
Halloween (`ir_control.py`: no press if HDMI already shows the wanted state,
retry once if the press was missed).

It is only `ir_control.py` + `ha_status.py` behind MQTT. No pygame, no GL, no
display, so it doesn't compete with FPP for HDMI.

## MQTT

| Topic | Direction | Payload |
|---|---|---|
| `house/projector/command` | HA → both Pis | `on` / `off` (or any word demoneye accepts, or `{"state":"on"}`) |
| `house/projector/<left\|right>/command` | HA → one Pi | same |
| `house/status/projector_<node>/state` | Pi → HA, retained | `{"power": "on\|off\|unknown", "result": "...", "desired": "on\|off\|none", "changed": ISO}` |
| `house/status/projector_<node>/availability` | Pi → HA, retained, LWT | `online` / `offline` |

`result` goes `turning_on` → `on`, or `turning_off` → `off`. A press the
projector never answered (after `retries`) ends in `failed_on` / `failed_off`.
That is the "verify" a Node-RED flow should wait on. `power` is the live HDMI
state, polled every `projector_poll_s`.

Publish commands **without retain**. A retained command is ignored, so a reboot
can't toggle a projector by replaying an old message.

Home Assistant gets a **Window Projector Left/Right** device on its own, by MQTT
discovery: `switch.window_projector_left` (state from HDMI), the
`sensor.window_projector_left_result` enum and a `Running` binary sensor. It's
a separate device from Demon Eye Left/Right, so the eye entities just show
offline for the season.

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

From anywhere on the LAN:

```bash
mosquitto_sub -h 192.168.86.5 -t 'house/status/projector_+/state' -v &
mosquitto_pub -h 192.168.86.5 -t house/projector/command -m on
```

## Check with FPP

FPP remotes play video out of the same HDMI port. Test once that a remote
which booted with its projector **off** still plays video after this service
turns the projector on and the master starts a playlist. If it doesn't, FPP is
deciding about the display at boot, and the show flow needs to restart fppd on
the remotes after power-on, or power the projectors on earlier.
