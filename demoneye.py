#!/usr/bin/env python3
"""
Synchronized projector show for Halloween: eyes, or figures crossing between
windows — on one or more Raspberry Pis, kept in step over MQTT.

  brain:     runs a director, streams state at PUBLISH_HZ, renders it
  follower:  renders whatever state it receives, with the same smoothing

    python demoneye.py --role brain    [--eye demon|human|walker] [--windowed] [--solo]
    python demoneye.py --role follower [--mode stage --eye ghost|mansion|spirits ...]

Everything tunable lives in settings.py / config.ini. What gets drawn is a
Renderer (renderers.py); this file is only the window, the network, the loop
and the watchdog.
"""

import argparse
import json
import os
import random
import signal
import sys
import time

# The Pi's GPU speaks GLES; make PyOpenGL and SDL use EGL there (before import).
if sys.platform.startswith("linux"):
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    os.environ.setdefault("SDL_VIDEO_X11_FORCE_EGL", "1")

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

import settings as S
from directors import _parse_command, _parse_target
from glutil import build_program, make_fullscreen_triangle
from renderers import make_renderer, EYE_FIGURES, STAGE_FIGURES


class EyeApp:
    def __init__(self, role, windowed, solo, eye="demon", show_stats=False, mode="eyes"):
        self.role = role
        self.solo = solo
        self.running = True
        self.renderer = make_renderer(mode, eye)
        self.schema = self.renderer.schema
        self.lerp = self.renderer.lerp
        self.net = dict(self.schema)      # latest authoritative state
        self.disp = dict(self.schema)     # smoothed on-screen state
        self.t = 0.0
        self.last_state_at = time.time()  # watchdog: when the follower last heard the brain
        self.stale = False
        # playback gate: solo has no controller so always plays; otherwise start
        # per START_MODE and let MQTT commands toggle it.
        self.active = True if solo else (S.START_MODE == "playing")
        signal.signal(signal.SIGTERM, lambda *_: self.stop())

        # Network BEFORE the display. If the projector is off its HDMI port
        # reads as disconnected and there's no display to open yet; with MQTT
        # already running, an "on" command can still power the projector over
        # IR, and the display appears once it's up. Opening the display first
        # deadlocked: the app died before hearing the command that fixes it.
        self.director = self.renderer.make_director() if role == "brain" else None
        self.client = None
        if not solo and mqtt is not None:
            self._setup_mqtt()
        self._pub_interval = 1.0 / S.PUBLISH_HZ
        self._last_pub = 0.0
        if self.active:
            self._set_projector(True)   # booting straight into the show

        self._open_window(windowed, eye)
        self.prog = build_program(self.renderer.frag, self.gles, self.renderer.defines)
        self.vao = make_fullscreen_triangle()
        GL.glUseProgram(self.prog)
        self.renderer.bind(self.prog)

        self.stats = None
        if show_stats and stats_mod:
            self.stats = stats_mod.Stats()
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
                    pygame.display.set_mode((1280, 720), flags)
                else:
                    pygame.display.set_mode((0, 0), flags | (pygame.FULLSCREEN if S.FULLSCREEN else 0))
                if waited:
                    print(f"[display] found a display after waiting {waited:.0f}s", flush=True)
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
                    print(f"[display] no display yet ({msg}) — projector off or HDMI unplugged? "
                          f"retrying every {retry:.0f}s. Tip: video=HDMI-A-1:1920x1080@60D in "
                          f"/boot/firmware/cmdline.txt keeps the port on with the projector off.",
                          flush=True)
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
        self.client.on_message = self._on_message
        try:
            self.client.connect(S.MQTT_HOST, S.MQTT_PORT, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            print(f"[mqtt] connect failed: {e} — running solo", file=sys.stderr)
            self.client = None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[mqtt] connected rc={reason_code}", flush=True)
        client.subscribe(S.COMMAND_TOPIC, qos=1)     # both roles listen for on/off
        if self.role == "brain":
            client.subscribe(S.TARGET_TOPIC, qos=0)  # only the brain takes targets
        else:
            client.subscribe(S.TOPIC, qos=0)

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
                self.renderer.on_target(self.director, _parse_target(msg.payload))
            return
        try:
            d = json.loads(msg.payload)
            self.net = {k: float(d.get(k, self.schema[k])) for k in self.schema}  # atomic
            self.last_state_at = time.time()
        except Exception:
            pass

    def _publish(self, state):
        if self.client:
            self.client.publish(S.TOPIC, json.dumps({k: round(state[k], 4) for k in self.schema}), qos=0)

    # -- main loop --
    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            dt = min(clock.tick(S.FPS) / 1000.0, 0.05)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT or (
                        ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q)):
                    self.stop()

            if self.director is not None:
                self.net = dict(self.director.update(dt))
                now = time.time()
                if now - self._last_pub >= self._pub_interval:
                    self._publish(self.net)
                    self._last_pub = now
            else:
                self._watchdog()

            self.t += dt
            self._smooth(dt)
            self.renderer.after_smooth(self, dt)
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
            self.client.loop_stop()
            self.client.disconnect()      # clean DISCONNECT so the broker isn't left waiting
        # Don't wait on SDL/EGL teardown: on some drivers it never returns, which
        # turns `systemctl stop` into a 90 s wait and a SIGKILL. The kernel
        # reclaims the GL context and DRM master when the process ends.
        sys.stdout.flush()
        os._exit(0)

    def _watchdog(self):
        """A follower that has stopped hearing from the brain fades to black
        instead of freezing on its last frame for the rest of the night."""
        if self.solo or S.STALE_AFTER_S <= 0 or not self.client:
            return
        stale = (time.time() - self.last_state_at) > S.STALE_AFTER_S
        if stale != self.stale:
            self.stale = stale
            print("[sync] no state from the brain — going dark" if stale
                  else "[sync] brain is back", flush=True)

    def _smooth(self, dt):
        # frame-rate-independent lerp toward the authoritative state
        for k in self.schema:
            k_lerp = 1.0 - (1.0 - self.lerp.get(k, 0.4)) ** (dt * S.FPS)
            self.disp[k] += (self.net[k] - self.disp[k]) * k_lerp

    def _draw(self):
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


def main():
    default_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    ap = argparse.ArgumentParser(description="Halloween projector show")
    ap.add_argument("--role", choices=["brain", "follower"], required=True)
    ap.add_argument("--mode", choices=["eyes", "stage"], default="eyes",
                    help="eyes = an eye per projector; stage = the projectors are "
                         "viewports onto one wide stage figures move across")
    ap.add_argument("--eye", default="demon",
                    help=f"eyes mode: {'|'.join(EYE_FIGURES)}.  "
                         f"stage mode: {'|'.join(STAGE_FIGURES)}")
    ap.add_argument("--windowed", action="store_true", help="run in a window (testing)")
    ap.add_argument("--solo", action="store_true", help="no MQTT (single-machine test)")
    ap.add_argument("--config", default=default_cfg, help="path to config.ini")
    ap.add_argument("--host", help="override MQTT host from config")
    ap.add_argument("--start", choices=["playing", "idle"],
                    help="override start mode: render now, or wait for an on command")
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
    if not args.solo and mqtt is None:
        sys.exit("paho-mqtt not installed; use --solo to test without it.")
    try:
        app = EyeApp(args.role, args.windowed, args.solo, eye=args.eye,
                     show_stats=args.stats, mode=args.mode)
    except ValueError as e:
        sys.exit(str(e))
    app.run()


if __name__ == "__main__":
    main()
