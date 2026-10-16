#!/usr/bin/env bash
# Offline tests for ~/bin/tg-status (the pinned status board). Exit 0 = all pass.
# Everything runs against a temp state dir (TG_STATUS_DIR), a fixture TODO (TG_STATUS_TODO) and a loopback mock
# Bot API (mock_tg.py, TG_STATUS_API + a fake TG_STATUS_TEST_TOKEN), so no call ever reaches Telegram and the real
# board/state in ~/.local/state/tg-status is never read or written. TG_STATUS_DEBOUNCE=0 renders synchronously,
# so no trailing render process outlives the test. Hooks run under a fake `claude` parent process (with or
# without --channels) to exercise the main-session detection.
# TG_STATUS_BIN=<path> tests another copy (e.g. an edited one before installing it).
# Synthetic profile (tests never read the real one): repo layout tests/profile, live layout ~/opt/profile-tests.
_pf="$(cd "$(dirname "$0")/.." && pwd)"; [ -f "$_pf/profile/fixtures/profile.json" ] && _pf=$_pf/profile || _pf=$_pf/profile-tests
export ASSISTANT_PROFILE="$_pf/fixtures/profile.json"
set -uo pipefail
TGS=${TG_STATUS_BIN:-/home/node/bin/tg-status}
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/tg-status-tests.XXXXXX")
MOCK_PID=
cleanup() { [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
mkdir -p "$T/mock" "$T/p" "$T/fakebin"
python3 -I "$HERE/mock_tg.py" "$T/mock" & MOCK_PID=$!
for _ in $(seq 50); do [ -s "$T/mock/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/mock/port" 2>/dev/null) || { echo "mock server did not start"; exit 1; }

TOKEN="123456789:TEST_ONLY_not_a_real""_token_abcdefgh"   # split so secretscan does not flag the fake
export TG_STATUS_DIR=$T/state TG_STATUS_TODO=$T/TODO.md TG_STATUS_API=http://127.0.0.1:$PORT \
       TG_STATUS_TEST_TOKEN=$TOKEN TG_STATUS_TEST_CHAT=4242 TG_STATUS_DEBOUNCE=0 TGS T
unset TG_STATUS_TRACE CLAUDE_JOB_DIR

pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2" | head -30; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if [[ "$2" == *"$3"* ]]; then ok "$1"; else no "$1" "$2"; fi; }
hasnt() { if [[ "$2" == *"$3"* ]]; then no "$1" "$2"; else ok "$1"; fi; }
CALLS=$T/mock/calls.jsonl
ncalls() { [ -f "$CALLS" ] && wc -l < "$CALLS" | tr -d ' ' || echo 0; }
# calls since line N: "method method ..."
methods_since() { tail -n +"$(( $1 + 1 ))" "$CALLS" 2>/dev/null | python3 -I -c 'import json,sys; print(" ".join(json.loads(l)["method"] for l in sys.stdin if l.strip()))'; }
# field of the last call: last_call 'expr on c (the call dict)'
last_call() { tail -n 1 "$CALLS" | python3 -I -c 'import json,sys; c = json.loads(sys.stdin.read()); print('"$1"')'; }
st() { python3 -I -c 'import json,sys; s = json.load(open(sys.argv[1])); print('"$1"')' "$T/state/state.json"; }
first_line() { "$TGS" show --plain | head -n 1; }

cat > "$T/TODO.md" <<'EOF'
# TODO
## Due
- **Pay rent** — 18:00 / 15.10
## In progress
- **Build tests** — tg-status suite running
  - internal sub-bullet, never shown
- Plain item — with status
- **Leak test** — @LEAK@
## Waiting on you
- **Pick a drive** — 2 options
- **Approve X**
- **R&D <x>** — check
## Ideas
- **Not shown**
EOF
sed -i "s/@LEAK@/987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZ""abcdef1234/" "$T/TODO.md"   # bot-token-shaped fake, split for secretscan

echo "# help and usage"
out=$("$TGS" --help); rc=$?
eq "--help rc 0" "$rc" "0"
has "--help prints the usage" "$out" "tg-status refresh | render"
[ -e "$T/state" ] && no "--help creates no state dir" || ok "--help creates no state dir"
"$TGS" -h >/dev/null; eq "-h rc 0" "$?" "0"
err=$("$TGS" bogus 2>&1 >/dev/null); rc=$?
eq "unknown command rc 2" "$rc" "2"
has "unknown command prints usage on stderr" "$err" "tg-status hook [event]"

echo "# show (render only, no Telegram)"
out=$("$TGS" show --plain); rc=$?
eq "show rc 0" "$rc" "0"
eq "header: idle, counts, first in-progress title" "$(printf '%s\n' "$out" | head -n 1)" "💤 ❗1 ❓3 · Idle · Build tests"
has "due section" "$out" "❗ Due:
• Pay rent"
has "in progress with status" "$out" "• Build tests — tg-status suite running"
has "plain bullet split on the dash" "$out" "• Plain item — with status"
has "waiting on you" "$out" "❓ Waiting on you:
• Pick a drive
• Approve X"
hasnt "sub-bullets skipped" "$out" "internal sub-bullet"
hasnt "other sections skipped" "$out" "Not shown"
has "bot-token-like text is scrubbed" "$out" "Leak test — <token>"
hasnt "token digits never shown" "$out" "987654321:ABC"
has "links line" "$out" "📊 Dashboard https://dash.example.org · 🖥 Browser https://browser.example.org/"
has "footer clock" "$out" "updated "
html=$("$TGS" show)
has "HTML: bold section labels" "$html" "<b>❗ Due:</b>"
has "HTML: text escaped" "$html" "R&amp;D &lt;x&gt;"
has "HTML: link anchors" "$html" '<a href="https://dash.example.org">📊 Dashboard</a>'
eq "show made no Telegram call" "$(ncalls)" "0"
eq "missing TODO: plain idle header" "$(TG_STATUS_TODO=$T/nope.md "$TGS" show --plain | head -n 1)" "💤 · Idle"

echo "# note: first render sends and pins"
"$TGS" note "Night build running"; rc=$?
eq "note rc 0" "$rc" "0"
eq "sendMessage then pinChatMessage" "$(methods_since 0)" "sendMessage pinChatMessage"
eq "only the test token is used" "$(python3 -I -c 'import json,sys; print(sorted({json.loads(l)["token"] for l in open(sys.argv[1])}))' "$CALLS")" "['$TOKEN']"
eq "sent to the test chat, silently" "$(head -n 1 "$CALLS" | python3 -I -c 'import json,sys; p = json.loads(sys.stdin.read())["params"]; print(p["chat_id"], p["parse_mode"], p["disable_notification"])')" "4242 HTML True"
has "note shown under the header" "$(head -n 1 "$CALLS" | python3 -I -c 'import json,sys; print(json.loads(sys.stdin.read())["params"]["text"])')" "📌 Night build running"
eq "pinned the new message" "$(last_call 'c["params"]["message_id"]')" "101"
eq "message id remembered" "$(st 's["message_id"]')" "101"

echo "# refresh: edit only when something changed"
n=$(ncalls); "$TGS" refresh; eq "unchanged: no call" "$(ncalls)" "$n"
printf -- '- **Renew domain** — 20.10\n' > "$T/due.add"
python3 -I - "$T/TODO.md" "$T/due.add" <<'EOF'
import sys
p, add = sys.argv[1], open(sys.argv[2]).read()
s = open(p).read().replace('## In progress\n', add + '## In progress\n', 1)
open(p, 'w').write(s)
EOF
n=$(ncalls); "$TGS" refresh
eq "changed TODO: one edit" "$(methods_since "$n")" "editMessageText"
eq "edit targets the pinned message" "$(last_call 'c["params"]["message_id"]')" "101"
has "edit carries the new item and count" "$(last_call 'c["params"]["text"]')" "❗2"
n=$(ncalls); "$TGS" note --clear
hasnt "note --clear removes the note" "$(last_call 'c["params"]["text"]')" "📌"
eq "note --clear: one edit" "$(methods_since "$n")" "editMessageText"

echo "# Telegram failures"
touch "$T/mock/fail-html"; "$TGS" note "html fallback"; rm -f "$T/mock/fail-html"
eq "HTML rejected: retried as plain text" "$(last_call '(c["method"], "parse_mode" in c["params"], "<b>" in c["params"]["text"])')" "('editMessageText', False, False)"
touch "$T/mock/fail-edit-notfound"; n=$(ncalls); "$TGS" note "message gone"; rm -f "$T/mock/fail-edit-notfound"
eq "pinned message gone: send a new one and pin it" "$(methods_since "$n")" "editMessageText sendMessage pinChatMessage"
eq "new message id remembered" "$(st 's["message_id"]')" "102"
touch "$T/mock/fail-429-once"; n=$(ncalls); "$TGS" note "rate limited"
eq "429 with retry_after: retried once" "$(methods_since "$n")" "editMessageText editMessageText"
has "429: the retry is logged" "$(cat "$T/state/log")" "429 on editMessageText, retrying in 1s"
out=$(TG_STATUS_API=http://127.0.0.1:1 "$TGS" note "api down" 2>&1); rc=$?
eq "API unreachable: rc 0 (logged, not fatal)" "$rc" "0"
has "API unreachable: logged" "$(cat "$T/state/log")" "telegram: "
hasnt "the token never reaches the log" "$(cat "$T/state/log")" "TEST_ONLY_not_a_real_token"
n=$(ncalls); err=$(env -u TG_STATUS_TEST_TOKEN "$TGS" note "no token" 2>&1); rc=$?
eq "test API without a test token: rc 1" "$rc" "1"
has "test API without a test token: says it failed" "$err" "tg-status note failed"
eq "test API without a test token: no call at all" "$(ncalls)" "$n"

echo "# reset"
n=$(ncalls); "$TGS" reset; rc=$?
eq "reset rc 0" "$rc" "0"
eq "reset: unpin + mark the old one, send + pin a new one" "$(methods_since "$n")" "unpinChatMessage editMessageText sendMessage pinChatMessage"
has "reset: old message says it moved" "$(sed -n "$(( n + 2 ))p" "$CALLS" | python3 -I -c 'import json,sys; print(json.loads(sys.stdin.read())["params"]["text"])')" "Status board moved to a newer message."
eq "reset: new message id" "$(st 's["message_id"]')" "103"

echo "# hooks: only the main session (claude with --channels) drives the board"
: > "$T/TODO.md"
"$TGS" note --clear
cat > "$T/fakebin/claude" <<'EOF'
#!/bin/bash
# fake claude: argv[1] is its flag (--channels=... or --print), the rest is a command it runs as its child
shift
"$@"; rc=$?
exit $rc
EOF
chmod +x "$T/fakebin/claude"
cat > "$T/p/start.json" <<'EOF'
{"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}
EOF
cat > "$T/p/prompt.json" <<'EOF'
{"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "<channel source=\"plugin:telegram-plus:telegram\" chat_id=\"1\">hi</channel>"}
EOF
cat > "$T/p/bash.json" <<'EOF'
{"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls", "description": "Run the test suite"}}
EOF
cat > "$T/p/grep.json" <<'EOF'
{"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Grep", "tool_input": {"pattern": "needle"}}
EOF
cat > "$T/p/agent-pre.json" <<'EOF'
{"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Agent", "tool_use_id": "tu1", "tool_input": {"description": "Write tests", "subagent_type": "general-purpose"}}
EOF
cat > "$T/p/agent-post.json" <<'EOF'
{"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Agent", "tool_use_id": "tu1", "tool_input": {"description": "Write tests"}, "tool_response": {"agentId": "a1", "isAsync": true, "status": "async_launched", "description": "Write tests"}}
EOF
cat > "$T/p/sub-step.json" <<'EOF'
{"hook_event_name": "PreToolUse", "session_id": "s1", "agent_id": "a1", "agent_type": "general-purpose", "tool_name": "Bash", "tool_input": {"description": "SUBAGENT STEP never shown"}}
EOF
cat > "$T/p/stop.json" <<'EOF'
{"hook_event_name": "Stop", "session_id": "s1", "background_tasks": [{"id": "a1", "type": "subagent", "status": "running", "description": "Write tests"}]}
EOF
cat > "$T/p/stopfail.json" <<'EOF'
{"hook_event_name": "StopFailure", "session_id": "s1", "error": "rate limit reached"}
EOF
cat > "$T/seq-main.sh" <<'EOF'
h() { "$TGS" hook < "$T/p/$1.json"; }
h start; h prompt; "$TGS" show --plain > "$T/s-prompt.txt"
h bash; "$TGS" show --plain > "$T/s-bash.txt"
h grep; "$TGS" show --plain > "$T/s-grep.txt"
h agent-pre; h agent-post; h sub-step; "$TGS" show --plain > "$T/s-agent.txt"
h stop; "$TGS" show --plain > "$T/s-stop.txt"
echo broken-json | "$TGS" hook; echo "$?" > "$T/s-badjson.rc"
EOF
n=$(ncalls)
"$T/fakebin/claude" --channels=plugin:telegram-plus@home-plugins bash "$T/seq-main.sh"
eq "prompt from Telegram: working" "$(head -n 1 "$T/s-prompt.txt")" "🟢 · Reading your Telegram message"
eq "tool with a description: its description" "$(head -n 1 "$T/s-bash.txt")" "🟢 · Run the test suite"
eq "Grep: search label" "$(head -n 1 "$T/s-grep.txt")" "🟢 · Searching for “needle”"
eq "async agent: delegating, one in the background" "$(head -n 1 "$T/s-agent.txt")" "🟢 ⚙1 · Delegating: Write tests"
has "background agent listed with its age" "$(cat "$T/s-agent.txt")" "⚙ Background:
• Write tests (<1m)"
hasnt "a subagent's own steps never show" "$(cat "$T/s-agent.txt")" "SUBAGENT STEP"
eq "Stop: idle, the running agent stays" "$(head -n 1 "$T/s-stop.txt")" "💤 ⚙1 · Idle"
eq "broken hook JSON: rc 0 (hooks fail silently)" "$(cat "$T/s-badjson.rc")" "0"
has "hooks rendered to Telegram as they went" "$(methods_since "$n")" "editMessageText"
eq "main session remembered" "$(st 's["main"]["session_id"], s["mode"]')" "s1 idle"

before=$(md5sum < "$T/state/state.json"); n=$(ncalls)
"$T/fakebin/claude" --print bash -c '"$TGS" hook < "$T/p/prompt.json"; "$TGS" hook < "$T/p/bash.json"'
eq "a claude without --channels (worker) is ignored: state untouched" "$(md5sum < "$T/state/state.json")" "$before"
eq "a worker's hooks make no Telegram call" "$(ncalls)" "$n"

echo "# refresh: stale main session and stale working"
"$TGS" refresh
eq "main process gone: background agents cleared" "$(first_line)" "💤 · Idle"
"$T/fakebin/claude" --channels=plugin:telegram-plus@home-plugins bash -c '"$TGS" hook < "$T/p/prompt.json"; "$TGS" hook < "$T/p/stopfail.json"'
eq "StopFailure: error shown in the header" "$(first_line)" "⚠️ · error: rate limit reached"
python3 -I - "$T/state/state.json" <<'EOF'
import json, sys, time
p = sys.argv[1]; s = json.load(open(p))
s.update(mode='working', now='Old work', last_activity=time.time() - 700, main={}, agents={})
s.pop('error', None)
json.dump(s, open(p, 'w'))
EOF
eq "fixture: working" "$(first_line)" "🟢 · Old work"
"$TGS" refresh
eq "working with no activity for 10+ min: idle" "$(first_line)" "💤 · Idle"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
