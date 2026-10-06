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
MQTT_HOST = "192.168.1.10"          # your broker (Home Assistant / Mosquitto)
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
TOPIC = "halloween/eyes/state"
COMMAND_TOPIC = "halloween/eyes/command"   # Home Assistant publishes on/off here
TARGET_TOPIC = "halloween/eyes/target"     # a sensor bridge publishes look-here targets
PUBLISH_HZ = 50                     # how often the brain streams state

# --- Playback control ---
# "playing" renders as soon as the app starts; "idle" shows black and waits for
# an "on" command. Either way the app always listens, so Home Assistant can
# start/stop it at any time.
START_MODE = "playing"

# --- Follower watchdog ---
# A follower that stops hearing from the brain fades to black after this many
# seconds instead of freezing on its last frame all night. 0 disables it.
STALE_AFTER_S = 2.0

# --- No display at startup ---
# If the projector is off its HDMI port reads as disconnected. Instead of
# exiting, wait and retry every DISPLAY_RETRY_S seconds (MQTT is already
# running, so an "on" command can still power the projector). 0 = exit.
DISPLAY_RETRY_S = 5.0

# --- Projector IR control (optional; config.ini [projector]) ---
IR_ENABLED = False

# --- Stage mode (--mode stage; config.ini [stage]) ---
# The projectors become viewports onto ONE virtual stage wider than any window.
# This Pi shows the slice of the stage between STAGE_FROM and STAGE_TO.
STAGE_FROM = 0.0
STAGE_TO = 0.4
FIG_SCALE = 0.72        # figure height as a fraction of the window
STAGE_SPEED = 0.085     # stage units per second
STAGE_FREEZE_WHEN_WATCHED = False   # only move while nobody is detected
FIGURE_FACES = "right"  # which way the SOURCE sprite frames face
FIGURE_STRIDE = 0.075   # stage distance covered by ONE full walk cycle
FIGURE_ATLAS = ""       # sprite atlas PNG (from sprite_atlas.py); selects the sprite shader

# Spirit trails are drawn through a HISTORY of past positions (a velocity-based
# trail can only ever be straight). Each renderer keeps its own history of the
# smoothed position it displayed, so both Pis match with no extra streaming.
SPIRIT_HIST_N = 8       # points per trail (N-1 segments)
SPIRIT_HIST_DT = 0.055  # seconds between samples — sets the trail's length

# --- Target tracking (brain only; eyes mode) ---
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
                    "gaze_scale": str(GAZE_SCALE_X), "gaze_scale_y": str(GAZE_SCALE_Y)},
        "stage": {"stage_from": str(STAGE_FROM), "stage_to": str(STAGE_TO),
                  "figure_scale": str(FIG_SCALE), "speed": str(STAGE_SPEED),
                  "freeze_when_watched": str(STAGE_FREEZE_WHEN_WATCHED),
                  "figure_atlas": FIGURE_ATLAS, "figure_faces": FIGURE_FACES,
                  "figure_stride": str(FIGURE_STRIDE)},
        "control": {"start": START_MODE, "target_hold": str(TARGET_HOLD),
                    "track_look_down": str(TRACK_LOOK_DOWN),
                    "stale_after_s": str(STALE_AFTER_S),
                    "display_retry_s": str(DISPLAY_RETRY_S)},
        "projector": dict(_PROJECTOR_DEFAULTS, ir_enabled=str(IR_ENABLED)),
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

    g["STAGE_FROM"] = cp.getfloat("stage", "stage_from")
    g["STAGE_TO"] = cp.getfloat("stage", "stage_to")
    g["FIG_SCALE"] = cp.getfloat("stage", "figure_scale")
    g["STAGE_SPEED"] = cp.getfloat("stage", "speed")
    g["STAGE_FREEZE_WHEN_WATCHED"] = cp.getboolean("stage", "freeze_when_watched")
    g["FIGURE_FACES"] = cp.get("stage", "figure_faces").strip().lower()
    g["FIGURE_STRIDE"] = cp.getfloat("stage", "figure_stride")
    atlas = cp.get("stage", "figure_atlas").strip()
    if atlas and not os.path.isabs(atlas):
        atlas = os.path.join(os.path.dirname(os.path.abspath(path or ".")), atlas)
    g["FIGURE_ATLAS"] = atlas

    g["IR_ENABLED"] = cp.getboolean("projector", "ir_enabled")

    g["EYE_CENTER_X"] = cp.getfloat("display", "center_x")
    g["EYE_CENTER_Y"] = cp.getfloat("display", "center_y")
    g["EYE_SCALE"] = cp.getfloat("display", "scale")
    g["MIRROR"] = cp.getboolean("display", "mirror")
    g["MIRROR_GAZE"] = cp.getboolean("display", "mirror_gaze")
    g["GAZE_SCALE_X"] = cp.getfloat("display", "gaze_scale")
    g["GAZE_SCALE_Y"] = cp.getfloat("display", "gaze_scale_y")
    return cp
