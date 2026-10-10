#!/usr/bin/env python3
"""
Synchronized projector eyes for Halloween: one eye per projector, on two
Raspberry Pis kept in step over MQTT.

  brain:     runs a director, streams state at PUBLISH_HZ, renders it
  follower:  renders whatever state it receives, with the same smoothing

    python demoneye.py --role brain    [--eye demon|human|walker] [--windowed] [--solo]
    python demoneye.py --role follower [--eye ...]

Everything tunable lives in settings.py / config.ini. The eye itself is the
EyeRenderer (renderers.py) and its shader (eye_shaders.py); this file is only
the window, the network, the loop and the watchdog.
"""

import argparse
import json
import math
import os
import random
import signal
import socket
import sys
import time

# The Pi's GPU speaks GLES; make PyOpenGL and SDL use EGL there (before import).
if sys.platform.startswith("linux"):
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    os.environ.setdefault("SDL_VIDEO_X11_FORCE_EGL", "1")
    # Leave SDL's KMSDRM driver triple-buffered (its default). Measured on the
    # Pi 5 at 720p: SDL_VIDEO_DOUBLE_BUFFER=1 serialises wait-for-flip, CPU and
    # GPU, so a frame just overran 16.7 ms and locked at 30 fps. Triple-buffered,
    # CPU and GPU overlap and it sustains 66; the loop is paced to the refresh
    # rate below so it renders 60, one per refresh, nothing thrown away.

import pygame
from OpenGL import GL

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # allowed in --windowed --solo test mode

try:
    import ir_control
except ImportError:
    ir_control = None

try:
    import stats as stats_mod
except ImportError:
    stats_mod = None

try:
    import ha_status
except ImportError:
    ha_status = None

import settings as S
from directors import _parse_command, _parse_target
from glutil import build_program, make_fullscreen_triangle
from renderers import make_renderer, EYE_FIGURES



def output_size(mode):
    """config output_mode -> (w, h) for set_mode. "1280x720" -> (1280, 720);
    "native", "auto", "" or anything unparseable -> (0, 0) = the projector's
    preferred mode. SDL's KMSDRM driver switches the real HDMI mode."""
    try:
        w, h = (int(v) for v in str(mode).lower().replace(" ", "").split("x"))
        if w > 0 and h > 0:
            return (w, h)
    except ValueError:
        pass
    return (0, 0)


def publish_every(fps, hz):
    """Publish once every N rendered frames. Counting frames (not checking a
    timer) keeps the rate exact: a 50 Hz timer against 60 fps frames 16.7 ms
    apart only fires every OTHER frame, i.e. 30 msg/s. 60 fps, 50-60 Hz → every
    frame (60/s); 30 Hz → every 2nd frame."""
    return max(1, round(fps / max(1.0, hz)))


def set_nodelay(client):
    """Turn off Nagle's algorithm on the MQTT socket. paho leaves it on, so the
    kernel may hold each small 50 Hz state message until the previous one is
    ACKed, bunching delivery into 40-200 ms clumps: the follower eye lags.
    Called on every (re)connect."""
    try:
        sock = client.socket()
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return True
    except (OSError, AttributeError):
        pass
    return False

class EyeApp:
    def __init__(self, role, windowed, solo, eye="walker", show_stats=False):
        self.role = role
        self.solo = solo
        self.eye = eye
        self.running = True
        self.display_up = False           # False while waiting for the projector's HDMI
        self.vsync = False
        self.status = None                # ha_status.StatusPublisher, when MQTT is up
        self.renderer = make_renderer(eye)
        self.schema = self.renderer.schema
        self.lerp = self.renderer.lerp
        self.net = dict(self.schema)      # latest authoritative state
        self.disp = dict(self.schema)     # smoothed on-screen state
        self.t = 0.0
        self.last_state_at = time.monotonic()  # watchdog: when the follower last heard the brain
        self.stale = False
        # playback gate: solo has no controller so always plays; otherwise start
        # per START_MODE and let MQTT commands toggle it.
        self.active = True if solo else (S.START_MODE == "playing")
        self._install_signal_handlers()

        # Network BEFORE the display. If the projector is off its HDMI port
        # reads as disconnected and there's no display to open yet; with MQTT
        # already running, an "on" command can still power the projector over
        # IR, and the display appears once it's up. Opening the display first
        # deadlocked: the app died before hearing the command that fixes it.
        self.director = self.renderer.make_director() if role == "brain" else None
        self.client = None
        if not solo and mqtt is not None:
            self._setup_mqtt()
        self._pub_every = publish_every(S.FPS, S.PUBLISH_HZ)
        self._frame_n = 0
        if self.active:
            self._set_projector(True)   # booting straight into the show

        self._open_window(windowed, eye)
        self.prog = build_program(self.renderer.frag, self.gles, self.renderer.defines)
        self.vao = make_fullscreen_triangle()
        GL.glUseProgram(self.prog)
        self.renderer.bind(self.prog)
        self._setup_render_target()

        self.stats = None
        if show_stats and stats_mod:
            self.stats = stats_mod.Stats(refresh_hz=getattr(self, "refresh_hz", None))
            print(self.stats.describe(), flush=True)
        elif show_stats:
            print("[stats] stats.py not found", flush=True)

    # -- window / GL context --
    def _open_window(self, windowed, eye):
        if S.SDL_DRIVER:
            os.environ["SDL_VIDEODRIVER"] = S.SDL_DRIVER
        # Only the display subsystem. pygame.init() would also start the audio
        # mixer and joystick — pointless here, noisy on a Pi with no sound card
        # (the "ALSA lib ... cannot find card" spam), and its teardown can hang
        # the process on exit so systemctl stop takes 90s and a SIGKILL.
        # Explicit init also reports a display failure clearly rather than as a
        # later "video system not initialized".
        # GLES 3.0 on the Pi (its GPU tops out at desktop GL 3.1 but supports
        # GLES 3.1), desktop core 3.3 on macOS. EYE_GL=es|core forces it.
        force = os.environ.get("EYE_GL", "").lower()
        self.gles = (force == "es") or (force != "core" and sys.platform != "darwin")
        prof = pygame.GL_CONTEXT_PROFILE_ES if self.gles else pygame.GL_CONTEXT_PROFILE_CORE
        flags = pygame.OPENGL | pygame.DOUBLEBUF

        # No display yet (projector off / unplugged)? Wait for one instead of
        # exiting — a crash loop can never receive the command that powers the
        # projector on. Windowed (desktop testing) still fails immediately.
        retry = 0 if windowed else max(0.0, S.DISPLAY_RETRY_S)
        waited, last_msg = 0.0, None
        while True:
            try:
                pygame.display.init()
                pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, prof)
                pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
                pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0 if self.gles else 3)
                if windowed:
                    size, mflags = (1280, 720), flags
                else:
                    size = output_size(S.OUTPUT_MODE)
                    mflags = flags | (pygame.FULLSCREEN if S.FULLSCREEN else 0)
                # vsync: each flip waits for the display's refresh, so every
                # rendered frame is shown exactly once. Without it frames land
                # out of step with the projector (some shown twice, some never):
                # judder, even at a high fps number.
                try:
                    pygame.display.set_mode(size, mflags, vsync=1)
                    self.vsync = True
                except pygame.error:
                    pygame.display.set_mode(size, mflags)
                    self.vsync = False
                if waited:
                    print(f"[display] found a display after waiting {waited:.0f}s", flush=True)
                self.display_up = True
                break
            except pygame.error as e:
                pygame.display.quit()
                drv = os.environ.get("SDL_VIDEODRIVER", "(auto)")
                if not retry:
                    raise SystemExit(
                        f"[fatal] could not initialise video (SDL_VIDEODRIVER={drv}): {e}\n"
                        "        If running as a systemd service on the console, the user "
                        "needs the display: check it is in the 'video' and 'render' groups, "
                        "that the Pi boots to console (no desktop holding the display), and "
                        "that /dev/dri exists.")
                msg = str(e)
                if msg != last_msg or waited % 60 < retry:      # log on change, then ~once a minute
                    print(f"[display] no display yet ({msg}) — normal while the projector is "
                          f"off; retrying every {retry:.0f}s", flush=True)
                    last_msg = msg
                slept = 0.0
                while slept < retry and self.running:           # stay responsive to SIGTERM
                    time.sleep(0.25); slept += 0.25
                waited += slept
                if not self.running:
                    self._exit_now()
        pygame.display.set_caption(f"{eye.capitalize()}")
        try:
            pygame.mouse.set_visible(False)   # only valid once a mode is set
        except pygame.error:
            pass
        self.W, self.H = pygame.display.get_surface().get_size()
        self.out_W, self.out_H = self.W, self.H     # the screen; W/H become the render size
        want = output_size(S.OUTPUT_MODE)
        note = ""
        if not windowed and want != (0, 0) and want != (self.W, self.H):
            note = f" (asked for {want[0]}x{want[1]}; the projector may not offer it)"
        try:
            self.refresh_hz = float(pygame.display.get_current_refresh_rate()) or None
        except (AttributeError, pygame.error):
            self.refresh_hz = None
        print(f"[gl] screen {self.W}x{self.H}"
              f"{f' @ {self.refresh_hz:.0f} Hz' if self.refresh_hz else ''}"
              f", vsync {'on' if getattr(self, 'vsync', False) else 'OFF'}{note}", flush=True)
        print(f"[gl] sdl_driver={pygame.display.get_driver()} "
              f"pyopengl={os.environ.get('PYOPENGL_PLATFORM', 'default')}", flush=True)
        print(f"[gl] {GL.glGetString(GL.GL_VERSION).decode()} "
              f"({'GLES' if self.gles else 'core'})", flush=True)

    # -- projector --
    def _set_projector(self, on):
        """Power the projector to match playback, on a worker thread so an IR
        send (and any warm-up wait) never stalls the render loop."""
        if not (S.IR_ENABLED and ir_control):
            return
        import threading
        threading.Thread(target=ir_control.power_on if on else ir_control.power_off,
                         daemon=True).start()

    # -- MQTT --
    def _setup_mqtt(self):
        cid = f"demoneye-{self.role}-{random.randint(0, 9999)}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        if S.MQTT_USER:
            self.client.username_pw_set(S.MQTT_USER, S.MQTT_PASS)
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_message = self._on_message
        self._setup_status()              # sets the last will, so before connect()
        # Connect in the background and keep retrying. After a power cut the
        # Pis can boot before the broker; a blocking connect() that failed
        # once used to leave us running solo all night (paho only retries on
        # its own after a first successful connect).
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        try:
            self.client.connect_async(S.MQTT_HOST, S.MQTT_PORT, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            print(f"[mqtt] can't start MQTT: {e} — running solo", file=sys.stderr)
            self.client = None
            self.status = None
            return
        if self.status:
            self.status.start()

    # -- Home Assistant status (MQTT discovery; see ha_status.py) --
    def _setup_status(self):
        if not (S.STATUS_ENABLED and ha_status):
            return
        node = ha_status.resolve_node(S.STATUS_NODE)
        self._host = ha_status.hostname()
        probe = ir_control.projector_state if ir_control else (lambda: None)
        self._projector = ha_status.CachedProbe(probe, S.STATUS_PROJECTOR_POLL_S)
        disc = ha_status.eye_discovery(
            node, S.STATUS_BASE_TOPIC, S.STATUS_DISCOVERY_PREFIX,
            command_topic=S.COMMAND_TOPIC if self.role == "brain" else None)
        self.status = ha_status.StatusPublisher(
            self.client, node, disc, self._status_snapshot,
            base=S.STATUS_BASE_TOPIC, prefix=S.STATUS_DISCOVERY_PREFIX,
            refresh_s=S.STATUS_REFRESH_S)
        print(f"[status] Home Assistant status as '{node}' on "
              f"{S.STATUS_BASE_TOPIC}/{node}/ (discovery: {S.STATUS_DISCOVERY_PREFIX})", flush=True)

    def _status_snapshot(self):
        return {
            "show": ha_status.show_state(self.active, self.display_up, self.stale),
            "display": "up" if self.display_up else "waiting",
            "projector": ha_status.projector_word(self._projector()),
            "role": self.role, "eye": self.eye,
            "version": ha_status.VERSION, "host": self._host,
        }

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:        # e.g. bad credentials: don't claim "online"
            print(f"[mqtt] broker refused the connection: {reason_code} — check "
                  "MQTT_USER/MQTT_PASS (/etc/demoneye.env)", flush=True)
            return
        print(f"[mqtt] connected rc={reason_code}", flush=True)
        set_nodelay(client)
        client.subscribe(S.COMMAND_TOPIC, qos=1)     # both roles listen for on/off
        if self.role == "brain":
            client.subscribe(S.TARGET_TOPIC, qos=0)  # only the brain takes targets
        else:
            client.subscribe(S.TOPIC, qos=0)
        if self.status:
            self.status.on_connect()

    def _on_connect_fail(self, client, userdata):
        print(f"[mqtt] can't reach {S.MQTT_HOST}:{S.MQTT_PORT} — retrying", flush=True)

    def _on_message(self, client, userdata, msg):
        if msg.topic == S.COMMAND_TOPIC:
            cmd = _parse_command(msg.payload)
            if cmd is not None and cmd != self.active:
                self.active = cmd
                print(f"[cmd] {'playing' if cmd else 'idle (black)'}", flush=True)
                self._set_projector(cmd)
            return
        if msg.topic == S.TARGET_TOPIC:
            if self.director is not None:
                t = _parse_target(msg.payload)
                self._log_target(t, msg.payload)
                self.renderer.on_target(self.director, t)
            return
        try:
            d = json.loads(msg.payload)
            net = {k: float(d.get(k, self.schema[k])) for k in self.schema}
        except Exception:
            return
        # json accepts NaN/Infinity, and one NaN would stick in the smoothed
        # state (disp += (NaN - disp)*k) until restart. Drop the whole message;
        # it doesn't count as hearing from the brain either.
        if not all(math.isfinite(v) for v in net.values()):
            now = time.monotonic()
            if now - getattr(self, "_bad_state_at", -1e9) > 60:
                print(f"[sync] ignoring state with non-finite values: {msg.payload[:80]!r}", flush=True)
                self._bad_state_at = now
            return
        self.net = net                       # atomic swap
        self.last_state_at = time.monotonic()

    def _log_target(self, t, payload):
        """One log line when tracking starts and one when it ends (not one per
        message), so `journalctl -t demoneye` shows whether targets arrived."""
        now = time.time()
        last = getattr(self, "_target_seen_at", 0.0)
        if t is None:
            if now - getattr(self, "_target_bad_at", 0.0) > 60:
                print(f"[track] unreadable target message: {payload[:80]!r}", flush=True)
                self._target_bad_at = now
        elif t[0] == "set":
            if now - last > 5.0:
                print(f"[track] target received  x={t[1]:+.2f} y={t[2]:+.2f}", flush=True)
            self._target_seen_at = now
        elif last:
            print("[track] target released", flush=True)
            self._target_seen_at = 0.0

    def _publish(self, state):
        if self.client:
            self.client.publish(S.TOPIC, json.dumps({k: round(state[k], 4) for k in self.schema}), qos=0)

    # -- main loop --
    def run(self):
        clock = pygame.time.Clock()
        # Frame pacing (measured on the Pi 5, 720p demon eye, 60 Hz projector):
        #   cap   tick() capped just above the refresh rate: 66 fps, 0 late. Best.
        #   exact sleep to exactly the refresh rate: 17 fps. It fights the
        #         driver's own wait for the page flip (two clocks drifting),
        #         so frames keep just missing and wait 4 refreshes.
        #   off   no limit (driver flips only).
        # SDL_VIDEO_DOUBLE_BUFFER=1 (env) locked it at 30 fps.
        hz = getattr(self, "refresh_hz", None) or S.FPS
        pacing = getattr(self, "pacing", "cap")
        cap = {"cap": round(hz * 1.1), "off": 0}.get(pacing, 0)
        period = 1.0 / hz
        next_t = time.perf_counter()
        print(f"[gl] frame pacing: {pacing}", flush=True)
        while self.running:
            if pacing == "exact":
                now = time.perf_counter()
                if next_t > now:
                    time.sleep(next_t - now)
                next_t = max(next_t + period, time.perf_counter() - period)
            dt = min(clock.tick(cap) / 1000.0, 0.05)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT or (
                        ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q)):
                    self.stop()

            if self.director is not None:
                self.net = dict(self.director.update(dt))
                self._frame_n += 1
                if self._frame_n % self._pub_every == 0:
                    self._publish(self.net)
            else:
                self._watchdog()

            self.t += dt
            self._smooth(dt)
            self._draw()
            pygame.display.flip()

            if self.stats:
                line = self.stats.frame()
                if line:
                    print(f"\r{line}  {self.renderer.stats_label(self, self.disp)}   ",
                          end="", flush=True)

        if self.stats:
            print()
        self._exit_now()

    def _exit_now(self):
        if self.client:
            if self.status:
                self.status.go_offline()  # a clean stop doesn't fire the last will
            self.client.loop_stop()
            self.client.disconnect()      # clean DISCONNECT so the broker isn't left waiting
        # Don't wait on SDL/EGL teardown: on some drivers it never returns, which
        # turns `systemctl stop` into a 90 s wait and a SIGKILL. The kernel
        # reclaims the GL context and DRM master when the process ends.
        sys.stdout.flush()
        os._exit(0)

    def _watchdog(self):
        """A follower that has stopped hearing from the brain cuts to black
        instead of freezing on its last frame for the rest of the night."""
        if self.solo or S.STALE_AFTER_S <= 0 or not self.client:
            return
        stale = (time.monotonic() - self.last_state_at) > S.STALE_AFTER_S
        if stale != self.stale:
            self.stale = stale
            print("[sync] no state from the brain — going dark" if stale
                  else "[sync] brain is back", flush=True)

    def _smooth(self, dt):
        # frame-rate-independent lerp toward the authoritative state
        for k in self.schema:
            k_lerp = 1.0 - (1.0 - self.lerp.get(k, 0.4)) ** (dt * S.FPS)
            self.disp[k] += (self.net[k] - self.disp[k]) * k_lerp

    # -- reduced-resolution rendering (config [display] render_height) --
    def _setup_render_target(self):
        """Draw into an offscreen texture of render_height lines, then scale it
        up to the screen in one cheap blit. The fragment shader runs once per
        rendered pixel, so 720 instead of 1080 is ~2.25x less GPU work."""
        self.fbo = None
        rh = int(S.RENDER_HEIGHT or 0)
        if rh <= 0 or rh >= self.out_H:
            print(f"[gl] rendering at {self.out_W}x{self.out_H} (native)", flush=True)
            return
        w, h = max(1, round(self.out_W * rh / self.out_H)), rh
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                     (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
        fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
        ok = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        if not ok:
            print("[gl] offscreen target not supported — rendering at native size", flush=True)
            return
        self.fbo, self.W, self.H = fbo, w, h
        print(f"[gl] rendering at {w}x{h}, scaled up to {self.out_W}x{self.out_H}", flush=True)

    def _draw(self):
        fbo = getattr(self, "fbo", None)
        if fbo is None or not self.active or self.stale:
            if fbo is not None:
                GL.glViewport(0, 0, self.out_W, self.out_H)
            self._render()
            return
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glViewport(0, 0, self.W, self.H)
        self._render()
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, fbo)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, 0)
        GL.glViewport(0, 0, self.out_W, self.out_H)
        GL.glBlitFramebuffer(0, 0, self.W, self.H, 0, 0, self.out_W, self.out_H,
                             GL.GL_COLOR_BUFFER_BIT, GL.GL_LINEAR)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)

    def _render(self):
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if not self.active or self.stale:      # idle or orphaned: pure black
            return
        GL.glUseProgram(self.prog)
        self.renderer.set_uniforms(self, self.disp)
        GL.glBindVertexArray(self.vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

    def stop(self):
        self.running = False

    def _install_signal_handlers(self):
        # systemctl stop (SIGTERM) and Ctrl-C (SIGINT) take the same clean path:
        # finish the frame, then _exit_now() (HA status offline, MQTT
        # disconnect, no SDL teardown). A second Ctrl-C force-quits if that hangs.
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        signal.signal(signal.SIGINT, self._on_sigint)

    def _on_sigint(self, *_):
        if not self.running:
            raise KeyboardInterrupt
        print("\n[exit] stopping (Ctrl-C again to force)", flush=True)
        self.stop()


def main():
    default_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    ap = argparse.ArgumentParser(description="Halloween projector show")
    ap.add_argument("--role", choices=["brain", "follower"], required=True)
    # --mode is gone (the app only draws eyes); still accepted as "eyes" so an
    # older service line with --mode eyes keeps working.
    ap.add_argument("--mode", choices=["eyes"], default="eyes", help=argparse.SUPPRESS)
    ap.add_argument("--eye", default="walker", choices=EYE_FIGURES,
                    help="which eye to draw (default walker), or 'christmas' for "
                         "snow and Santa's sleigh")
    ap.add_argument("--windowed", action="store_true", help="run in a window (testing)")
    ap.add_argument("--solo", action="store_true", help="no MQTT (single-machine test)")
    ap.add_argument("--config", default=default_cfg, help="path to config.ini")
    ap.add_argument("--host", help="override MQTT host from config")
    ap.add_argument("--start", choices=["playing", "idle"],
                    help="override start mode: render now, or wait for an on command")
    ap.add_argument("--render-height", type=int, metavar="N",
                    help="override [display] render_height (0 = output resolution); for testing")
    ap.add_argument("--output-mode", metavar="WxH",
                    help="override [display] output_mode, e.g. 1280x720 or native; for testing")
    ap.add_argument("--pacing", choices=["cap", "exact", "off"], default="cap",
                    help="frame pacing (testing): cap = default, exact, off")
    ap.add_argument("--stats", action="store_true",
                    help="print FPS / CPU / GPU / temperature while running")
    args = ap.parse_args()

    cp = S.load_config(args.config)
    if ir_control and S.IR_ENABLED:
        ir_control.configure(cp["projector"])
    if args.host:
        S.MQTT_HOST = args.host
    if args.start:
        S.START_MODE = args.start
    if args.render_height is not None:
        S.RENDER_HEIGHT = args.render_height
    if args.output_mode:
        S.OUTPUT_MODE = args.output_mode
    if not args.solo and mqtt is None:
        sys.exit("paho-mqtt not installed; use --solo to test without it.")
    try:
        app = EyeApp(args.role, args.windowed, args.solo, eye=args.eye,
                     show_stats=args.stats)
        app.pacing = args.pacing
    except ValueError as e:
        sys.exit(str(e))
    app.run()


if __name__ == "__main__":
    main()
