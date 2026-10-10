# Tracking people, and powering the projectors

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

### mmWave sensor bridge (`ha_bridge.py`)

`ha_bridge.py` turns an mmWave presence sensor's target tracking into those
target messages. It connects to Home Assistant over its WebSocket API, watches
the sensor's per-target X/Y coordinates (LD2450-based, up to 3 targets — an S1 Pro Multi Sense or Everything Presence Pro both work), picks the
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
; false = always publish
follow_command = true
; quiet until a command arrives
start_enabled = false
```

While the show is off the bridge publishes **nothing** to MQTT and also
**closes its Home Assistant connection** (otherwise HA would keep sending it
every state change in the house). *On* reconnects and re-reads the sensor
within a second or two; while on, it only publishes when someone is in view
(up to `publish_hz`, plus a `heartbeat_s` refresh for someone standing still).
To take the bridge out of the show entirely: `sudo systemctl disable --now
ha-bridge` on the Pi that runs it.

Keep your command messages **retained** so the bridge (and the Pis) pick up the
current state within a second of starting, rather than waiting for the next
transition. When it's told to stop, it publishes a `clear` first so the eyes
don't keep staring at a target that will never update again.

The LD2450 radar reaches ~6m with a fan-shaped, top-down field of view, so
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
; send the code N times if the projector misses presses
repeat = 1
; set 1 if POWER must be pressed twice to confirm shutdown
off_confirm = 1
; seconds between the press and the confirming press
off_confirm_gap_s = 2.0
; wait this long after power-on before the eye appears
warmup_s = 0
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


> **Units matter.** The name says mm, but `x_range_mm` is really "how many of
> the sensor's units equal a full eye swing". The S1 Pro reports in a scale
> where ~250 gave a full sweep across a room at 10ft; an EP Pro reporting raw
> millimetres wants ~3000. If the eye barely moves off centre, lower it; if it
> pins at the edges, raise it. `python track_test.py monitor` shows the live
> values while you walk.

### Sensor away from the eyes (`geometry = true`)

If the sensor is mounted somewhere other than at the eyes (say, at the side
of the driveway, turned to cover the street), set `geometry = true` in
`bridge.ini` `[calibration]` and describe where it is. Picture it from above,
standing at the house facing the street: origin = the house wall midway between
the two eyes, +X = your right, +Y = straight out toward the street.

For each target the sensor reports `(x, y)` (x = to its right, y = ahead):

```
u = x * sensor_unit_mm      (negated if sensor_flip_x)
v = y * sensor_unit_mm
X = sensor_x_mm + u*cos(yaw) - v*sin(yaw)      yaw = sensor_yaw_deg, + = turned left
Y = sensor_y_mm + u*sin(yaw) + v*cos(yaw)
bearing = atan2(X, Y)                           angle from the eyes, + = to the right
gaze x  = -bearing / gaze_max_deg               clamped to -1..+1, flipped by invert_x
```

The minus on the last line is because the eyes face the street: a target to
the house's right is on the audience's left, so the pupil moves screen-left.
`x_range_mm` / `x_offset_mm` are ignored in this mode, and `y_from_distance`
uses the distance from the eyes (`hypot(X, Y)`) instead of the sensor's own.

Calibrate: (1) stand 2 m in front of the sensor, read target Y in HA, and set
`sensor_unit_mm = 2000 / reading`; (2) stand to the sensor's right, and if X
reads negative set `sensor_flip_x = true`; (3) walk the street with the show on
and set `invert_x` if the eyes look away from you. Tested in
`tests/test_bridge_geometry.py`.
