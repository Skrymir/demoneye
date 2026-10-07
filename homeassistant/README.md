# Home Assistant

Everything that lives on the Home Assistant side of the show: the status
entities the Pis publish, the dashboard card, alerts, and the on/off control.
Nothing in this folder runs on the Pis (`deploy.sh` doesn't copy it).

| File | What it is | Where it goes |
|---|---|---|
| [dashboard_card.yaml](dashboard_card.yaml) | Status card: health summary, both eyes, tracking | Dashboard → + Add card → Manual |
| [alert_automations.yaml](alert_automations.yaml) | Four alerts (projector, eye offline, bridge down, nobody tracked) | Settings → Automations |
| [show_schedule.yaml](show_schedule.yaml) | On at dusk / off at 11 pm, if HA schedules the show | Settings → Automations |
| [nodered_flow.json](nodered_flow.json) | Node-RED nodes that publish on/off, if Node-RED schedules it | Node-RED → Import |

## Show status

Each eye and the tracking bridge announce themselves to Home Assistant over
**MQTT discovery**: no add-on, no YAML sensors. Once the code is deployed, three
devices appear under Settings → Devices & services → MQTT:

| Device | Entity | Meaning |
|---|---|---|
| **Demon Eye Left** / **Right** | `binary_sensor.eye_left_running` | The eye process is up. Turns **off** on a clean stop at once, or via the MQTT last will about 45 s after a crash, power loss or network drop |
| | `binary_sensor.projector_left` | Projector really on (HDMI hot-plug state, read every 5 s). *Unknown* if a forced `video=...D` mode hides it |
| | `sensor.eyes_left_show_state` | `playing`, `idle` (show off, black), `waiting_for_display` (on, but the projector's HDMI isn't up yet), `lost_brain` (follower has stopped hearing the brain, so it's dark) |
| | `sensor.eyes_left_role` | `brain` / `follower`; attributes: `eye`, `display`, `version`, `host` |
| **Demon Eye Tracking Bridge** | `binary_sensor.eye_bridge_running` | `ha_bridge.py` is up (same last-will rules) |
| | `binary_sensor.tracking_active` | Someone is being tracked right now |
| | `sensor.last_target_seen` | When someone was last seen (timestamp). Kept while the show is off and across bridge restarts |
| | `sensor.bridge_host` | Which Pi runs the bridge; attributes `enabled` (following the show), `version` |
| | `binary_sensor.eye_bridge_ha_connected` | Bridge's WebSocket to HA is up. **Off while the show is off, by design** |
| **Halloween Eyes** | `switch.halloween_eyes_show` | Mirrors the command topic, so it follows Node-RED. Flipping it publishes `on`/`off` (retained) exactly as Node-RED does. Published by the brain only |

All the `left` entities exist for `right` too.

## Setup

1. **HA's MQTT integration must use the same broker** (Mosquitto at
   `192.168.86.5:1883`) with discovery on (the default; prefix `homeassistant`).
2. `./deploy.sh` from the Mac. Nothing to change on the Pis: each Pi's own
   `config.ini` / `bridge.ini` has no `[status]` section yet, and the built-in
   defaults are what you want. The two eyes name themselves `left` / `right`
   from their hostnames. `python3 check_config.py` confirms it ("HA status
   nodes: pi-eye-left.home -> left, pi-eye-right.home -> right").
3. The devices appear in HA within a few seconds of the services restarting.
4. **Dashboard card:** edit a dashboard → + Add card → Manual → paste
   [dashboard_card.yaml](dashboard_card.yaml).
   Built-in cards only. The top line is a health summary: "all good", or a
   list of what's wrong.
5. **Alerts:** [alert_automations.yaml](alert_automations.yaml)
   has four automations. Each one posts a persistent notification; uncomment
   the `notify.mobile_app_…` action to get it on your phone too.
   - A projector is still off 2 minutes after the show went on
   - An eye has been offline for a minute during show hours
   - The tracking bridge is down while the show is on
   - Three hours into the show and nobody has been tracked (sensor aim)

Entity IDs are set on **first** discovery only (`default_entity_id`). If HA
already had an entity with the same name, you may get a `_2` suffix: rename it
under Settings → Entities, or fix the ID in the card YAML.

## Turning the show on and off

Everything follows one retained (QoS 1) message on `halloween/eyes/command`:
`on` / `off` (also `play`/`stop`, `1`/`0`, or JSON `{"state": "ON"}`). Both
Pis, the projectors (over IR) and the tracking bridge react to it together,
and a Pi that reboots mid-evening picks up the current state at once.

Pick **one** scheduler:

- **Node-RED** (what's in use now): import [nodered_flow.json](nodered_flow.json)
  and wire your sun state machine into *Set ON* / *Set OFF*. Its broker node
  points at `192.168.86.5:1883`.
- **A Home Assistant automation:** [show_schedule.yaml](show_schedule.yaml)
  (on 15 minutes before sunset, off at 11 pm).

Either way, `switch.halloween_eyes_show` shows the current state and flips it
by hand. The next scheduled change overrides a manual one.

How the Pis behave before the first command (`start = idle` or `playing` in
`config.ini`) is covered in [docs/OPERATIONS.md](../docs/OPERATIONS.md#remote-control-home-assistant).

## MQTT topics

```
halloween/eyes/status/<node>/availability   online | offline   retained; LWT = offline
halloween/eyes/status/<node>/state          JSON               retained
halloween/eyes/status/bridge/last_seen      ISO-8601 UTC       retained, never cleared
homeassistant/<component>/demoneye_<node>_<thing>/config     retained discovery
```

`<node>` is `left`, `right` or `bridge`. State is published within a second of
a change plus a refresh every 30 s (eyes) / 60 s (bridge). It never goes at
frame rate, and the 60 Hz gaze stream stays on `halloween/eyes/state`. When HA
restarts (its `homeassistant/status` birth message), every node resends its
discovery and state.

Watch it live from any machine with the Mosquitto clients:

```bash
mosquitto_sub -h 192.168.86.5 -v -t 'halloween/eyes/status/#'
```

Example eye state:
```json
{"display": "up", "eye": "walker", "host": "pi-eye-left",
 "projector": "on", "role": "brain", "show": "playing", "version": "2026.10.06"}
```

## Config

`config.ini` (eyes) and `bridge.ini` (bridge) both take an optional
`[status]` section. Every key has a default, so leave it out unless you need
to change something.

| Key | Default | |
|---|---|---|
| `enabled` | `true` | `false` stops all status/discovery publishing from that process |
| `node` | `auto` (eyes) / `bridge` | Eyes: `auto` = `left`/`right` from the hostname. The two Pis must differ (`check_config.py` errors if not) |
| `base_topic` | `halloween/eyes/status` | Same on every node |
| `discovery_prefix` | `homeassistant` | Match HA's MQTT integration setting |
| `refresh_s` | `30` / `60` | Republish interval when nothing changes |
| `projector_poll_s` | `5` | Eyes only: how often the HDMI state is read |
| `last_seen_every_s` | `10` | Bridge only: how often `last_target_seen` updates while someone is held |

## Removing the entities

Delete the devices in HA. If they come back, clear the retained discovery
messages on the broker:

```bash
for t in $(mosquitto_sub -h 192.168.86.5 -t 'homeassistant/+/+/config' -v -W 2 \
           | awk '/demoneye_/ {print $1}'); do
  mosquitto_pub -h 192.168.86.5 -t "$t" -r -n
done
```
…and set `[status] enabled = false` on the nodes first, or they'll republish
on their next restart.
