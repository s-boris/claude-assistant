#!/usr/bin/env bash
# Offline tests for ~/bin/tg-cmd's instant-command table (bills, queue, space, reminders, home). Exit 0 = all pass.
# /home runs against a fake `ha` (TG_CMD_BIN) and the placeholder fixtures in fixtures/ (HA_HEALTH_DIR).
# /memory and /recall were retired 2026-10-13; their 140 tests are in ~/attic/2026-10-13/opt/tg-cmd-tests/run.sh.
set -uo pipefail
TGCMD=${TGCMD_BIN:-/home/node/bin/tg-cmd}
INSTANT=${INSTANT_JSON:-/home/node/.claude/channels/telegram/instant.json}
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2" | head -30; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if [[ "$2" == *"$3"* ]]; then ok "$1"; else no "$1" "$2"; fi; }
hasnt() { if [[ "$2" == *"$3"* ]]; then no "$1" "$2"; else ok "$1"; fi; }

echo "# command table"
out=$("$TGCMD" --instant-json); rc=$?
eq "instant-json rc 0" "$rc" "0"
eq "commands in menu order" "$(printf '%s' "$out" | python3 -c 'import json,sys; print([x["command"] for x in json.load(sys.stdin)])')" "['bills', 'queue', 'space', 'reminders', 'home']"
eq "all entries have exactly command/description/run" "$(printf '%s' "$out" | python3 -c 'import json,sys; print(all(set(x) == {"command","description","run"} for x in json.load(sys.stdin)))')" "True"
eq "run lines call tg-cmd" "$(printf '%s' "$out" | python3 -c 'import json,sys; print(all(x["run"] == "~/bin/tg-cmd " + x["command"] for x in json.load(sys.stdin)))')" "True"
if [ -f "$INSTANT" ]; then eq "instant.json matches the table" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[2])) == json.loads(sys.argv[1]))' "$out" "$INSTANT")" "True"
else echo "skip - instant.json matches the table (no live $INSTANT; set INSTANT_JSON to check one)"; fi
list=$("$TGCMD" --list)
has "--list shows /reminders" "$list" "/reminders  Your upcoming reminders"
has "--list shows /home" "$list" "/home       Home: lights, temperatures, media, HA health"
hasnt "--list: no /memory" "$list" "/memory"
hasnt "--list: no /recall" "$list" "/recall"

echo "# retired and bad commands"
"$TGCMD" memory >/dev/null 2>&1; eq "memory: unknown, rc 2" "$?" "2"
"$TGCMD" recall paperless >/dev/null 2>&1; eq "recall: rc 2" "$?" "2"
"$TGCMD" bogus >/dev/null 2>&1; eq "unknown command: rc 2" "$?" "2"
err=$("$TGCMD" bills extra 2>&1 >/dev/null); rc=$?
eq "arguments: rc 2" "$rc" "2"
has "arguments: says no arguments" "$err" "take no arguments"
"$TGCMD" --help >/dev/null; eq "--help rc 0" "$?" "0"

echo "# /home (fake ha, placeholder fixtures)"
FIX=$(cd "$(dirname "$0")" && pwd)/fixtures
T=$(mktemp -d)
trap 'find "$T" -mindepth 1 -delete; rmdir "$T"' EXIT
cat > "$T/ha" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_HA_LOG"
if [ -n "${FAKE_HA_ERR:-}" ]; then echo "ha: $FAKE_HA_ERR" >&2; exit 1; fi
cat "$FAKE_HA_JSON"
EOF
chmod +x "$T/ha"
home() { # $1 = ha fixture, $2 = health dir
  : > "$T/calls"
  TG_CMD_BIN="$T" TG_CMD_NOW="2031-03-05 12:00" FAKE_HA_LOG="$T/calls" FAKE_HA_JSON="$1" HA_HEALTH_DIR="$2" "$TGCMD" home
}
out=$(home "$FIX/home-full.json" "$FIX/health-new"); rc=$?
eq "home: rc 0" "$rc" "0"
want='🏠 Home · 12:00
💡 3 lights on · Kitchen 2, Study 1
🌡 Kitchen 20.4° · Lounge 21.5°→22°🔥 · Study 19.0° · Terrace 8.3°
🧹 Vacuum: docked · 5%
🎵 Playing: Speaker One (Radio App) · Tablet (mail me at <email>) · TV Box on (HDMI 1)
🩺 HA health: 4 open (1 new) · 2 known offline
↳ Study Desk Lamp 🆕, Study Old Sensor, select.extra_mode, +1 more
🔋 Low: Window low, Door Sensor 9%'
eq "home: full answer" "$out" "$want"
eq "home: at most 10 lines" "$(( $(printf '%s\n' "$out" | wc -l) <= 10 ))" "1"
eq "home: one ha read, nothing else" "$(cat "$T/calls")" "list --json --limit 5000"
for bad in "Test Person" "Workplace" "not_home" "Tracker" "Proximity" "@example.com" "Phone One" "3%" \
           "Robo Vac Battery" "Fridge" "CPU" "55.0" "Loose" "25.0" "Speaker Two" "Idle Speaker" "String Lights" "Kitchen Lights"; do
  hasnt "home: no '$bad'" "$out" "$bad"
done
out=$(home "$FIX/home-empty.json" "$FIX/health-clean"); rc=$?
eq "home quiet: rc 0" "$rc" "0"
want='🏠 Home · 12:00
💡 All lights off
🎵 Nothing playing
🩺 HA health: all ok · 2 known offline
🔋 Batteries ok'
eq "home quiet: no temperature/vacuum lines, all ok" "$out" "$want"
out=$(home "$FIX/home-full.json" "$FIX/health-base")
has "home baseline: nothing new" "$out" "🩺 HA health: 4 open · 2 known offline"
hasnt "home baseline: no new marker" "$out" "🆕"
out=$(home "$FIX/home-full.json" "$FIX/health-old")
has "home old snapshot: age + failing watch" "$out" "🩺 HA health: 4 open · 2 known offline (snapshot 4 d old) · watch failing"
out=$(home "$FIX/home-full.json" "$T/no-such-dir")
has "home no snapshot: says so" "$out" "🩺 HA health: no ha-health-watch snapshot yet"
has "home no snapshot: rest still shown" "$out" "💡 3 lights on"
out=$(FAKE_HA_ERR="HA unreachable at http://198.51.100.7:8123" home "$FIX/home-full.json" "$FIX/health-new"); rc=$?
eq "home ha down: rc 3" "$rc" "3"
has "home ha down: fallback" "$out" "⚠️ /home failed: Home Assistant"
has "home ha down: hint" "$out" "what's on at home?"
hasnt "home ha down: IP masked" "$out" "198.51.100.7"
printf 'not json' > "$T/garbage.json"
out=$(home "$T/garbage.json" "$FIX/health-new"); rc=$?
eq "home garbage: rc 3" "$rc" "3"
has "home garbage: unreadable" "$out" "unreadable answer"
printf '{"a": 1}' > "$T/obj.json"
home "$T/obj.json" "$FIX/health-new" >/dev/null; eq "home non-list: rc 3" "$?" "3"
"$TGCMD" home extra >/dev/null 2>&1; eq "home with arguments: rc 2" "$?" "2"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
