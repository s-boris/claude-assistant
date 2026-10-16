---
name: home-assistant
description: Control and query the owner's Home Assistant. Use for "turn off my TV", "lights on/off", "living room lights off", "dim the bedroom to 30%", "warmer light", "pause the music", "TV volume 20", "is the TV on", "which lights are on", "room lights", "run scene/script X", "all lights off", "start the vacuum". Uses ~/bin/ha (privacy-gated reads, allowlisted writes with read-back). Not for HA cleanup/config (ha-audit, ha-apply) or dashboards.
---
# Home Assistant control

Tool: `ha` (~/bin) is the ONLY way I read or switch things in his HA. Never import ha-audit, monkeypatch an allowlist, or call the HA API with ad-hoc scripts or curl (they bypass the privacy gate and can print his phone's geocoded address). Config and registry changes go through ha-apply and need his OK. They never go through ha.

## Steps
1. Find the entity. A name that matches several entities is refused, and ha lists the candidate ids. Pick one, or ask him if it's unclear.
   `ha find "words" [--domain light]`, `ha list light --area "Bedroom"`, `ha areas`, `ha state <id|name>`
2. Act (names can be unquoted words or ids; ids printed masked, e.g. `vacuum.<email>`, can be passed back as they are):
   - `ha on|off|toggle <id|name>`; room: `ha off --area "Living Room"` (lights; `--domain switch|fan|media_player` for others)
   - `ha set <light> --brightness 30 --color-temp 2700` (`--color red`; `--area` works too)
   - `ha media <player> play|pause|stop|next|prev|mute|unmute|volume 20`
   - `ha scene run <x>`, `ha script run all_lights_off`
   - anything else on the allowlist: `ha call media_player.select_source <id> '{"source": "HDMI1"}'`, `ha call vacuum.start <id>`
   - `-n` = dry run (resolves and shows what would be sent, sends nothing). Use it for testing, especially at night.
3. Read the result. ha prints `old -> new ok`. Exit 3 means it was sent but not confirmed within 8 s: say so, and check `ha state` again. Exit 2 means refused (ambiguous, not allowed or unavailable) and nothing was sent. Exit 1 means HA was unreachable or returned an error.
4. Reply in 1–2 lines ("TV off ✓"), or just react 🎉/👍.

## His setup
- Areas: Balcony, Bathroom, Bedroom, Hallway, Living Room. Only script: `script.all_lights_off`. No scenes, locks, alarms or covers.
- "TV" matches 15 entities. The LG OLED is `media_player.lg_webos_tv_oled55c7v` and shows unavailable when it's off, so HA can't switch it then. The Google TV Streamer is `media_player.living_room_tv` (also `remote.living_room_tv` and `…_tv_cast`). "Turn off my TV": if the LG is available, switch it off. If not, it's already off; then check the streamer.
- Sonos: `living_room` (Port), `living_room_left/right`, `bathroom`, `bedroom_speaker`, `sonos_move`. Home Theater (HEOS): `home_theater`, `home_theater_heos`.

## Safety (built into ha, and my rules)
- Allowlisted domains: light, switch, input_boolean, media_player, remote, scene, script, cover, climate, fan, vacuum and button, each with a fixed list of services. No reloads, no scene.apply/create, no target redirection in the JSON.
- Locks and alarms are never controlled by ha, and there is no override. If he asks, tell him ha won't and point him to the HA app (or offer to build a confirmed path).
- Garage, gate and door openers (covers, switches, buttons with such names) need his explicit confirmation each time. Only after he says yes to this action: `--owner-ok "his words"` (it's logged). Scenes or scripts that unlock or disarm are refused. If they open a garage, they need `--owner-ok`.
- Buttons only via `ha call button.press <id>`. Ask him first for restart, reboot or factory buttons.
- Switch only what he asked for. No live experiments: during quiet hours (00:00–08:00), only on his explicit request.

## Privacy
ha's output always passes the privacy gate (~/bin/lib/ha_privacy.py). It drops coordinates and address-like attributes, and masks emails, IPs, MACs, SSIDs and Bluetooth names. Trackers and persons show only a zone name or home/not_home. Geocoded, Wi-Fi, IP and notification sensors show `<hidden>`. His whereabouts come from `vault ha now` (context), not from ha. Don't relay person or tracker states unless he asks.

## Log, tests
- Every write: `~/.local/state/ha/actions.log` (JSON lines: time, service, entity_ids, data, result, old → new). For "what did you switch?", tail it.
- Changing ha: edit a copy, run `HA_BIN=/copy ~/opt/ha-tests/run.sh` (fake HA, 38 tests), then install with an atomic mv. A new domain or service needs his OK.
