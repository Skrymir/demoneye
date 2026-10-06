# Running the show

## Projector power, verified by HDMI

A projector that's switched off stops announcing itself on HDMI, so the Pi
sees the port as **disconnected**; switched on, **connected**. Two things use
that:

- **IR power is closed-loop** (`verify = true` in `[projector]`). Before any
  press, `ir_control` checks the HDMI state and does nothing if the projector
  is already where it should be — essential with a single POWER *toggle*,
  where a blind press on an already-on projector turns it off (e.g. a Pi that
  restarts mid-show and receives the retained *on*). After pressing it waits
  for the state to change (`on_timeout_s`, `off_timeout_s`), and if it didn't,
  re-checks and retries once (`retries`). Logs say
  `confirmed by HDMI after 6.2s`, `already on — not pressing`, or
  `GAVE UP ... check the IR transmitter's aim`.
  `python ir_control.py status` shows what the Pi currently sees.
- **The app waits for a display.** With the projector off there's nothing to
  open (`error getting KMSDRM displays information`), so the app connects to
  MQTT first and retries the display every `display_retry_s`. Node-RED's *on*
  powers the projector, HDMI comes up, the picture starts.

**Don't force the HDMI port on** (`video=HDMI-A-1:...D` in cmdline.txt) on a
Pi with IR verification: it makes the port always read *connected*, so the
real state is hidden and IR falls back to blind toggles. `check_config.py`
warns if it's set. (It's harmless on a Pi without IR.)

Check once, by hand, that your projector really drops HDMI when off:
```bash
cat /sys/class/drm/card1-HDMI-A-1/status    # projector off: disconnected
                                            # projector on:  connected
```
Some projectors keep HDMI alive in standby; if yours reads *connected* while
off, set `verify = false`.

## Bench test the two-Pi sync first

```bash
# Pi A                                          # Pi B
./venv/bin/python demoneye.py --role brain --windowed
                                                ./venv/bin/python demoneye.py --role follower --windowed
```

Both windows should move together. `--host 192.168.x.x` overrides the broker
on the command line if you want to test without editing config.ini. If the
follower shows nothing, check it's subscribed to the same `topic` and that the
brain is publishing (`python track_test.py monitor` shows the state stream).

**If the brain dies mid-show, the follower goes dark** after `stale_after_s`
(2 s by default) instead of freezing on its last frame, and comes back when
state resumes. You'll see `[sync] no state from the brain — going dark` in
its journal.

## 4. Run it on the projectors (KMSDRM, no desktop)

For a decoration you just want to power on, boot each Pi to the console and let
it draw straight to HDMI:

1. `sudo raspi-config` → **System Options → Boot / Auto Login → Console** (or
   Console Autologin). No desktop — a running desktop would hold the display and
   block KMSDRM.
2. Install the GL runtime libs (Lite is minimal and won't have these), and make
   sure your user can reach the GPU:
   ```bash
   sudo apt install -y libgles2 libegl1 libgbm1 libgl1-mesa-dri
   sudo usermod -aG video,render pi
   ```
3. Stop the screen from blanking: add `consoleblank=0` to the end of the single
   line in `/boot/firmware/cmdline.txt`, then reboot.
4. Install the service (edit `--role` first — **brain** on one Pi, **follower**
   on the other; set `User=` if not `joshc`):

   ```bash
   sudo cp demoneye.service /etc/systemd/system/demoneye.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now demoneye
   journalctl -u demoneye -f   # watch it start / see the GL + MQTT lines
   ```

Now both eyes come up on boot and self-sync as soon as the brain connects.

> **Service won't start: "kmsdrm not available".** The app runs fine from an SSH
> prompt but fails as a service. That's a service-context problem, in order of
> likelihood:
> 1. **A desktop is running and holds the display.** `systemctl get-default` —
>    if it says `graphical.target`, switch to console:
>    `sudo systemctl set-default multi-user.target && sudo reboot`
>    (or `sudo raspi-config` → System Options → Boot/Auto Login → Console).
> 2. **The service user can't reach the GPU.** A system service isn't part of a
>    login session, so it doesn't inherit the seat's device ACLs. The shipped
>    unit sets `SupplementaryGroups=video render input` and attaches to a VT
>    (`PAMName=login`, `TTYPath=/dev/tty1`, `StandardInput=tty`) so SDL can
>    become DRM master. Make sure your copy has those lines.
> 3. **SDL picked the wrong DRM card.** A Pi 5 exposes two: a render-only V3D
>    node and the one that actually drives HDMI. SDL defaults to index 0 and
>    reports "kmsdrm not available" if that's the render node. Find the display
>    card — it's the one with connectors:
>    ```bash
>    ls /sys/class/drm/          # the display card owns cardN-HDMI-A-1
>    ```
>    then set `SDL_KMSDRM_DEVICE_INDEX` to that N (the shipped unit sets 1):
>    ```bash
>    SDL_KMSDRM_DEVICE_INDEX=1 SDL_VIDEODRIVER=kmsdrm EYE_GL=es \
>      ./venv/bin/python demoneye.py --role brain --solo
>    ```
>    Card numbering isn't guaranteed stable across hardware, so set it
>    explicitly on each Pi rather than assuming both match.
> 4. **No GPU device at all.** `ls -l /dev/dri` should show a `card*` and
>    `renderD128`.
>
> Confirm it works interactively first — that isolates the display problem from
> the service problem:
> ```bash
> SDL_VIDEODRIVER=kmsdrm EYE_GL=es ./venv/bin/python demoneye.py --role brain --solo
> ```

> **How the GL path picks a context.** The Pi's GPU exposes desktop OpenGL only
> at 3.1 but OpenGL **ES** at 3.1, so on the Pi (any non-Mac) the app requests a
> **GLES 3.0** context and loads the `#version 300 es` shaders; on macOS it uses
> a desktop 3.3 core context and `#version 330`. Same shader bodies either way.
> Under the console (KMSDRM) the app sets `PYOPENGL_PLATFORM=egl` for you (the
> service already exports `SDL_VIDEODRIVER=kmsdrm`). Force a mode with
> `EYE_GL=es` or `EYE_GL=core` if you ever need to.
>
> **First-time GL check on a Pi** (do this before wiring up the service): from
> the console, `EYE_GL=es ./venv/bin/python demoneye.py --role brain --solo`.
> It prints a `[gl] OpenGL ES ... (GLES)` line and should show the eye on HDMI.
> If it errors, paste me the output — the shaders and render path are validated,
> so anything here is environment (a missing lib or a driver quirk), and the
> fallback is to run under the desktop instead (same code, different display).

> **Prefer to run from the desktop instead?** Skip KMSDRM: remove the
> `Environment=SDL_VIDEODRIVER=kmsdrm` line and start the script from your
> desktop session's autostart instead of systemd. KMSDRM is just the cleanest
> path for a headless projector.

---

## Remote control (Home Assistant)

The app can render on start, or boot **idle** (black) and wait to be turned on.
Either way it always listens on `command_topic` (default
`halloween/eyes/command`), so Home Assistant can start/stop the whole pair with
one message — both Pis subscribe.

Set the boot behaviour per Pi in `config.ini`:
```ini
[control]
; or "playing"
start = idle
```
For an HA-scheduled decoration, `idle` is usually right: the Pis power on dark
and wait for HA. (`--start playing|idle` overrides it on the command line.)

Commands are case-insensitive. **On:** `on` / `1` / `true` / `play` / `start`.
**Off:** `off` / `0` / `false` / `stop` / `pause`. A JSON `{"state": "ON"}` also
works (so an MQTT light/switch entity drives it directly).

**Publish the command retained** so a Pi that reboots mid-event immediately
picks up the current on/off state instead of waiting for the next automation.

Example HA automations (Settings → Automations, edit in YAML):
```yaml
- alias: "Halloween eyes ON at dusk"
  trigger: { platform: sun, event: sunset, offset: "-00:15:00" }
  action:
    - service: mqtt.publish
      data: { topic: "halloween/eyes/command", payload: "on", retain: true }

- alias: "Halloween eyes OFF at 11pm"
  trigger: { platform: time, at: "23:00:00" }
  action:
    - service: mqtt.publish
      data: { topic: "halloween/eyes/command", payload: "off", retain: true }
```
Prefer a switch you can also flip by hand? Add an `mqtt` switch pointing at the
same topic and have the automations toggle that instead.

---

## Checking performance (`--stats`)

Add `--stats` to print a live line of FPS, CPU, GPU and temperature — the
quickest way to confirm a Pi is holding framerate with the heavier shaders
(the walker's animated fire is the most expensive).

```bash
./venv/bin/python demoneye.py --role brain --solo --eye walker --stats
```
```
fps  60.0  min  59.4  worst  18.2ms  cpu  22.0%  gpu  38.5%  52.1C  [glare]
```

- **min** is the lowest one-second average since start, and **worst** the single
  longest frame — both stick, so you can leave it running and check later
  whether anything stuttered.
- The trailing tag is the brain's current mood, handy while testing tracking.
- **gpu** comes from the V3D debugfs counter on Pi 4/5 and reports the busiest
  queue (RENDER for shader work). If that isn't readable it falls back to the
  v3d clock via `vcgencmd`, and failing that shows `n/a` rather than a
  misleading number. Reading the counter may need root — run with `sudo` if you
  see `n/a` on a Pi.
- `pip install psutil` gives more accurate CPU numbers; without it the value
  comes from `/proc/stat`.

If FPS sags below ~55 on the Pi, the first things to trim are the walker's fire
(the two `fbm` layers in its flame block) and the `fbm` octave counts (5 -> 4).

---

## If sync ever looks off

It shouldn't — gaze is drift-proof by design. The one timing-sensitive moment is
the blink (~200 ms). Across a house with the projectors far apart it won't be
noticeable, but if you ever want it frame-perfect, the upgrade is: sync both Pis'
clocks (they already run NTP) and have the brain schedule blinks at a shared
future timestamp instead of streaming them. Say the word and I'll add it.

## Fallback: one Pi, two projectors

If the networked sync ever gives you trouble, a single Pi 5 has two HDMI outs and
can drive both projectors from one frame-locked process — perfect sync, zero
networking. The catch is cabling both projectors back to one spot, which you
noted is a problem here. It's the safety net, not the plan.
