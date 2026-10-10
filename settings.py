"""
All tunable settings in one place.

Defaults live here; per-machine values come from config.ini via load_config().
Every other module does `import settings as S` and reads S.<NAME>, so the
renderers, directors and app all see the same values with no circular imports.

Precedence: CLI flags  >  env MQTT_USER/MQTT_PASS  >  config.ini  >  defaults.
"""

import configparser
import os

# --- MQTT (defaults; real values live in config.ini [mqtt]) ---
MQTT_HOST = "192.168.86.5"          # your broker (Home Assistant / Mosquitto)
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
TOPIC = "house/eyes/state"
COMMAND_TOPIC = "house/show/command"   # Home Assistant publishes on/off here
TARGET_TOPIC = "house/tracking/target"     # a sensor bridge publishes look-here targets
PUBLISH_HZ = 60                     # brain streams state once per frame at 60 fps

# --- Playback control ---
# "playing" renders as soon as the app starts; "idle" shows black and waits for
# an "on" command. Either way the app always listens, so Home Assistant can
# start/stop it at any time.
START_MODE = "playing"

# --- Follower watchdog ---
# A follower that stops hearing from the brain cuts to black after this many
# seconds instead of freezing on its last frame all night. 0 disables it.
STALE_AFTER_S = 2.0

# --- No display at startup ---
# If the projector is off its HDMI port reads as disconnected. Instead of
# exiting, wait and retry every DISPLAY_RETRY_S seconds (MQTT is already
# running, so an "on" command can still power the projector). 0 = exit.
DISPLAY_RETRY_S = 5.0

# --- Projector IR control (optional; config.ini [projector]) ---
IR_ENABLED = False

# --- Home Assistant status over MQTT discovery (config.ini [status]) ---
# Each eye publishes retained, low-rate status (running / projector / show
# state) plus discovery configs, so its entities appear in HA by themselves.
STATUS_ENABLED = True
STATUS_NODE = "auto"                 # auto = left/right from the hostname
STATUS_BASE_TOPIC = "house/status"
STATUS_DISCOVERY_PREFIX = "homeassistant"
STATUS_REFRESH_S = 30.0              # republish even when nothing changed
STATUS_PROJECTOR_POLL_S = 5.0        # how often to read the HDMI (projector) state

# --- Target tracking (brain only) ---
TARGET_HOLD = 2.0       # seconds to keep tracking after the last target update
ALERT_DILATION = 0.20   # pupil while locked on (lower = more constricted)
# How the eye reacts the moment it notices someone. Weights need not sum to 1.
#   startle — flares wide in surprise, then settles into the locked glare
#   anger   — snaps straight to a narrowed, hostile glare (no flare)
#   slow    — barely reacts at first, then slowly hardens into the glare
REACT_WEIGHTS = {"startle": 0.5, "anger": 0.3, "slow": 0.2}
TRACK_SNAP = 0.10       # seconds to whip to a newly acquired target
TRACK_FOLLOW = 0.22     # follow responsiveness (0..1 per 60 fps frame)
# The eyes sit ABOVE the people they watch, so a level target is really below
# them. Tilts the gaze down while tracking: 0 = none, 1 = fully down.
TRACK_LOOK_DOWN = 0.35

# --- Per-Pi placement (config.ini [display]) ---
EYE_CENTER_X = 0.50     # 0=left .. 1=right (fraction of screen)
EYE_CENTER_Y = 0.50     # 0=top .. 1=bottom
EYE_SCALE = 1.00        # overall size multiplier
MIRROR = False          # flip this eye (glint side) for a symmetric pair; ONE Pi only
MIRROR_GAZE = False     # also flip gaze direction (cross-eyed pair). Leave False.
GAZE_SCALE_X = 1.0      # how far the gaze travels; 1.6-2.2 reads well on a house
GAZE_SCALE_Y = 1.0
# HDMI mode the Pi sends to the projector, e.g. "1280x720". The demon shader is
# heavy: at 1080p a Pi 5 can't hold 60 fps, at 720p it can, and on a house it
# looks the same (the projector does the scaling). "native" = whatever the
# projector prefers. Set in the app, so no boot-config changes are needed.
OUTPUT_MODE = "1280x720"
# Optional extra: draw below the output resolution and scale up on the GPU.
# 0 = draw at the output resolution (normal with a 720p output_mode).
RENDER_HEIGHT = 0

# --- Display ---
FULLSCREEN = True
FPS = 60
SDL_DRIVER = os.environ.get("SDL_VIDEODRIVER", "")   # "kmsdrm" = straight to HDMI

_PROJECTOR_DEFAULTS = {
    "ir_enabled": "false", "keymap": "/home/fpp/groview.toml", "device": "",
    "power_key": "KEY_POWER", "separate_on_off": "false", "on_key": "KEY_POWER",
    "off_key": "KEY_POWER", "repeat": "1", "off_confirm": "0",
    "off_confirm_gap_s": "2.0", "warmup_s": "0",
}


def load_config(path):
    """Load per-machine settings from config.ini over the defaults above.
    Returns the parsed ConfigParser (ir_control reads its section from it)."""
    g = globals()
    cp = configparser.ConfigParser()
    cp.read_dict({
        "mqtt": {"host": MQTT_HOST, "port": str(MQTT_PORT), "username": MQTT_USER,
                 "password": MQTT_PASS, "topic": TOPIC,
                 "command_topic": COMMAND_TOPIC, "target_topic": TARGET_TOPIC},
        "display": {"center_x": str(EYE_CENTER_X), "center_y": str(EYE_CENTER_Y),
                    "scale": str(EYE_SCALE), "mirror": str(MIRROR),
                    "mirror_gaze": str(MIRROR_GAZE),
                    "gaze_scale": str(GAZE_SCALE_X), "gaze_scale_y": str(GAZE_SCALE_Y),
                    "render_height": str(RENDER_HEIGHT), "output_mode": OUTPUT_MODE},
        "control": {"start": START_MODE, "target_hold": str(TARGET_HOLD),
                    "track_look_down": str(TRACK_LOOK_DOWN),
                    "stale_after_s": str(STALE_AFTER_S),
                    "display_retry_s": str(DISPLAY_RETRY_S)},
        "projector": dict(_PROJECTOR_DEFAULTS, ir_enabled=str(IR_ENABLED)),
        "status": {"enabled": str(STATUS_ENABLED), "node": STATUS_NODE,
                   "base_topic": STATUS_BASE_TOPIC,
                   "discovery_prefix": STATUS_DISCOVERY_PREFIX,
                   "refresh_s": str(STATUS_REFRESH_S),
                   "projector_poll_s": str(STATUS_PROJECTOR_POLL_S)},
    })
    if path and os.path.exists(path):
        cp.read(path)
        print(f"[config] loaded {path}", flush=True)
    else:
        print(f"[config] {path} not found — using built-in defaults", flush=True)

    g["MQTT_HOST"] = cp.get("mqtt", "host")
    g["MQTT_PORT"] = cp.getint("mqtt", "port")
    g["MQTT_USER"] = os.environ.get("MQTT_USER") or cp.get("mqtt", "username")
    g["MQTT_PASS"] = os.environ.get("MQTT_PASS") or cp.get("mqtt", "password")
    g["TOPIC"] = cp.get("mqtt", "topic")
    g["COMMAND_TOPIC"] = cp.get("mqtt", "command_topic")
    g["TARGET_TOPIC"] = cp.get("mqtt", "target_topic")

    start = cp.get("control", "start").strip().lower()
    g["START_MODE"] = start if start in ("playing", "idle") else "playing"
    g["TARGET_HOLD"] = cp.getfloat("control", "target_hold")
    g["TRACK_LOOK_DOWN"] = cp.getfloat("control", "track_look_down")
    g["STALE_AFTER_S"] = cp.getfloat("control", "stale_after_s")
    g["DISPLAY_RETRY_S"] = cp.getfloat("control", "display_retry_s")

    g["IR_ENABLED"] = cp.getboolean("projector", "ir_enabled")

    g["STATUS_ENABLED"] = cp.getboolean("status", "enabled")
    g["STATUS_NODE"] = cp.get("status", "node").strip() or "auto"
    g["STATUS_BASE_TOPIC"] = cp.get("status", "base_topic").strip().rstrip("/")
    g["STATUS_DISCOVERY_PREFIX"] = cp.get("status", "discovery_prefix").strip().rstrip("/")
    g["STATUS_REFRESH_S"] = cp.getfloat("status", "refresh_s")
    g["STATUS_PROJECTOR_POLL_S"] = cp.getfloat("status", "projector_poll_s")

    g["EYE_CENTER_X"] = cp.getfloat("display", "center_x")
    g["EYE_CENTER_Y"] = cp.getfloat("display", "center_y")
    g["EYE_SCALE"] = cp.getfloat("display", "scale")
    if not (0.0 < EYE_SCALE < float("inf")):  # 0 = divide by zero in every shader
        print(f"[config] [display] scale = {EYE_SCALE} is invalid — using 1.0", flush=True)
        g["EYE_SCALE"] = 1.0
    g["MIRROR"] = cp.getboolean("display", "mirror")
    g["MIRROR_GAZE"] = cp.getboolean("display", "mirror_gaze")
    g["GAZE_SCALE_X"] = cp.getfloat("display", "gaze_scale")
    g["GAZE_SCALE_Y"] = cp.getfloat("display", "gaze_scale_y")
    g["RENDER_HEIGHT"] = cp.getint("display", "render_height")
    g["OUTPUT_MODE"] = cp.get("display", "output_mode").strip()
    return cp
