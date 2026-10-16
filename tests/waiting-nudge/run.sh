#!/usr/bin/env bash
# Offline tests for ~/bin/waiting-nudge (cue to ask the owner about TODO.md "Waiting on you" items). Exit 0 = all pass.
# Inputs are fixtures via the script's own seams (WAITING_NUDGE_TODO / _STATE / _INBOX / _TODAY); HOME is a temp dir
# too, so a broken seam can't reach the real TODO.md, state or ~/inbox. The real state and inbox are checked at the end.
# Covers: parsing the section, list, state sync (since kept, removed items dropped), --cron (ONE note, 2-day rule,
# order, overwrite), asked (substring, case, counts, resets the clock), corrupt state, defaults under $HOME.
# WAITING_NUDGE_BIN=<path> tests another copy.
set -uo pipefail
W=${WAITING_NUDGE_BIN:-/home/node/bin/waiting-nudge}
REAL_STATE=/home/node/.local/state/waiting-nudge.json
REAL_INBOX=/home/node/inbox
T=$(mktemp -d "${TMPDIR:-/tmp}/waiting-nudge-tests.XXXXXX")
trap 'rm -rf "$T"' EXIT
export HOME=$T/home WAITING_NUDGE_TODO=$T/TODO.md WAITING_NUDGE_STATE=$T/state/sub/wn.json WAITING_NUDGE_INBOX=$T/inbox
mkdir -p "$HOME" "$T/inbox"
real_sum() { sha256sum "$REAL_STATE" 2>/dev/null | cut -d' ' -f1; }
real_notes() { ls "$REAL_INBOX" 2>/dev/null | grep -c '^waiting-nudge-'; }
real_before=$(real_sum); real_notes_before=$(real_notes)

pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2" | head -30; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if [[ "$2" == *"$3"* ]]; then ok "$1"; else no "$1" "$2"; fi; }
hasnt() { if [[ "$2" == *"$3"* ]]; then no "$1" "$2"; else ok "$1"; fi; }
ST=$WAITING_NUDGE_STATE
run() { WAITING_NUDGE_TODAY=$1 "$W" "${@:2}"; }            # run DATE ARGS...
js() { python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$ST" "$1"; }
notes() { ls -A "$T/inbox" | wc -l; }
reset() { rm -rf "$T/state" "$T/inbox"/*; }
LONG=$(printf 'L%.0s' $(seq 1 200))

cat > "$T/TODO.md" <<EOF
# TODO

## Due
- **Pay the dentist bill** — due 20.10

## In progress
- **Building the thing** — running

## Waiting on you
- **Tax extension OK?** — asked 12.10, needs his 👍
  - internal note: draft in ~/reports/tax
- **Seerr login** — waiting for the password
- Plain item without bold — still counts
- **$LONG**

## Ideas
- **Idea not waiting** — later
EOF

echo "# list and parsing"
reset
out=$(run 2026-10-10 2>"$T/err"); rc=$?
eq "list: rc 0" "$rc" "0"
eq "list: no stderr" "$(cat "$T/err")" ""
eq "list: one line per waiting item" "$(printf '%s\n' "$out" | wc -l)" "4"
eq "list: first line format" "$(printf '%s\n' "$out" | sed -n 1p)" "2026-10-10  asked never       Tax extension OK?"
has "list: bold title only, not the status" "$out" "  Seerr login"
hasnt "list: status after the dash is not part of the key" "$out" "waiting for the password"
has "list: plain item kept whole" "$out" "  Plain item without bold — still counts"
hasnt "list: sub-bullets ignored" "$out" "internal note"
hasnt "list: Due section ignored" "$out" "dentist"
hasnt "list: In progress ignored" "$out" "Building"
hasnt "list: Ideas (after the section) ignored" "$out" "Idea not waiting"
eq "list: long title cut to 160 chars" "$(js 'max(len(k) for k in s)')" "160"
eq "state: created in a new directory" "$([ -f "$ST" ] && echo yes)" "yes"
eq "state: valid JSON, 4 items" "$(js 'len(s)')" "4"
eq "state: new item since=today, asked=None" "$(js 's["Seerr login"]')" "{'since': '2026-10-10', 'asked': None}"
eq "state: no .tmp left behind (atomic save)" "$(ls -A "$T/state/sub")" "wn.json"
eq "list: no inbox note" "$(notes)" "0"

echo "# sync"
run 2026-10-12 > /dev/null
eq "sync: since kept on later runs" "$(js 's["Seerr login"]["since"]')" "2026-10-10"
sed -i 's/^- \*\*Seerr login\*\*.*/- **New thing** — fresh/' "$T/TODO.md"
out=$(run 2026-10-12)
eq "sync: removed item dropped from state" "$(js '"Seerr login" in s')" "False"
eq "sync: new item gets today's date" "$(js 's["New thing"]["since"]')" "2026-10-12"
eq "sync: others keep their since" "$(js 's["Tax extension OK?"]["since"]')" "2026-10-10"
eq "sync: still 4 items" "$(js 'len(s)')" "4"
sed -i 's/^- \*\*New thing\*\*.*/- **Seerr login** — waiting for the password/' "$T/TODO.md"

echo "# --cron and the 2-day rule"
reset
out=$(run 2026-10-10 --cron 2>"$T/err"); rc=$?
eq "cron day 0: rc 0" "$rc" "0"
eq "cron day 0: silent" "$out$(cat "$T/err")" ""
eq "cron day 0: no note" "$(notes)" "0"
run 2026-10-11 --cron
eq "cron day 1: no note (under 2 days)" "$(notes)" "0"
out=$(run 2026-10-12 --cron 2>&1); rc=$?
eq "cron day 2: rc 0, silent" "$rc:$out" "0:"
eq "cron day 2: exactly one note" "$(notes)" "1"
N=$T/inbox/waiting-nudge-2026-10-12.md
eq "cron: note named by date" "$([ -f "$N" ] && echo yes)" "yes"
has "cron: note explains the job" "$(cat "$N")" 'Then `waiting-nudge asked "<substring>"` for each item asked. Never in quiet hours.'
has "cron: note line format" "$(cat "$N")" "- Seerr login  (waiting since 2026-10-10, last asked never)"
eq "cron: all 4 due items listed" "$(grep -c '^- ' "$N")" "4"
hasnt "cron: other sections never in the note" "$(cat "$N")" "dentist"
eq "cron: state untouched apart from sync" "$(js 'sorted(set(v["asked"] for v in s.values()), key=str)')" "[None]"

echo "# asked"
out=$(run 2026-10-12 asked "SEERR"); rc=$?
eq "asked: case-insensitive substring, rc 0" "$rc:$out" "0:marked 1"
eq "asked: date recorded" "$(js 's["Seerr login"]["asked"]')" "2026-10-12"
eq "asked: since unchanged" "$(js 's["Seerr login"]["since"]')" "2026-10-10"
out=$(run 2026-10-12 asked "o"); eq "asked: several matches counted" "$out" "marked 3"
reset; run 2026-10-10 > /dev/null
out=$(run 2026-10-10 asked "zzz-nomatch"); eq "asked: no match -> marked 0" "$out" "marked 0"
eq "asked: no match changes nothing" "$(js 'sorted(set(v["asked"] for v in s.values()), key=str)')" "[None]"
out=$(run 2026-10-10 asked); hasnt "asked without substring marks nothing (lists)" "$out" "marked"
eq "asked without substring: state unchanged" "$(js 'sorted(set(v["asked"] for v in s.values()), key=str)')" "[None]"
run 2026-10-12 asked "tax" > /dev/null
run 2026-10-12 --cron
N=$T/inbox/waiting-nudge-2026-10-12.md
hasnt "cron: item asked today is not due" "$(cat "$N")" "Tax extension"
eq "cron: the other 3 are" "$(grep -c '^- ' "$N")" "3"
rm -f "$T/inbox"/*
run 2026-10-13 --cron
hasnt "cron: asked 1 day ago still not due" "$(cat "$T/inbox/waiting-nudge-2026-10-13.md")" "Tax extension"
run 2026-10-14 --cron
has "cron: asked 2 days ago is due again" "$(cat "$T/inbox/waiting-nudge-2026-10-14.md")" \
  "- Tax extension OK?  (waiting since 2026-10-10, last asked 2026-10-12)"
eq "cron: one note per day, older ones kept for me to handle" "$(notes)" "2"
first=$(grep '^- ' "$T/inbox/waiting-nudge-2026-10-14.md" | head -1)
has "cron: never-asked (oldest) items first" "$first" "last asked never"
eq "cron: last-asked item last" "$(grep '^- ' "$T/inbox/waiting-nudge-2026-10-14.md" | tail -1 | cut -c1-19)" "- Tax extension OK?"
run 2026-10-14 --cron
eq "cron: a second run the same day overwrites, still one note for the day" "$(ls "$T/inbox" | grep -c 2026-10-14)" "1"

echo "# all recently asked -> no note"
reset; run 2026-10-10 > /dev/null
run 2026-10-20 asked "" > /dev/null   # empty substring matches every item (how to mark all)
eq "asked '': marks every item" "$(js 'sorted(set(v["asked"] for v in s.values()))')" "['2026-10-20']"
run 2026-10-21 --cron
eq "cron: nothing due -> no note" "$(notes)" "0"

echo "# robustness"
reset; mkdir -p "$T/state/sub"; echo '{broken' > "$ST"
out=$(run 2026-10-10 2>"$T/err"); rc=$?
eq "corrupt state: rc 0, no traceback" "$rc:$(cat "$T/err")" "0:"
eq "corrupt state: rebuilt as valid JSON" "$(js 'len(s)')" "4"
cp "$T/TODO.md" "$T/TODO.full"
printf '# TODO\n\n## Waiting on you\n\n## Ideas\n- **x**\n' > "$T/TODO.md"
out=$(run 2026-10-30 --cron 2>&1); rc=$?
eq "empty section: rc 0, silent" "$rc:$out" "0:"
eq "empty section: state emptied" "$(js 'len(s)')" "0"
eq "empty section: no note" "$(notes)" "0"
printf '# TODO\n## Waiting on you (2)\n- **Not matched**\n' > "$T/TODO.md"
eq "heading must be exactly '## Waiting on you'" "$(run 2026-10-30)" ""
cp "$T/TODO.full" "$T/TODO.md"

echo "# defaults (no seams): everything under \$HOME"
H2=$T/home2; mkdir -p "$H2/workspace" "$H2/inbox"; cp "$T/TODO.md" "$H2/workspace/TODO.md"
old=$(date -d '3 days ago' +%F); today=$(date +%F)
out=$(env -u WAITING_NUDGE_TODO -u WAITING_NUDGE_STATE -u WAITING_NUDGE_INBOX -u WAITING_NUDGE_TODAY HOME=$H2 "$W")
has "default: reads \$HOME/workspace/TODO.md" "$out" "Seerr login"
eq "default: state in \$HOME/.local/state" "$([ -f "$H2/.local/state/waiting-nudge.json" ] && echo yes)" "yes"
has "default: today's real date" "$out" "$today  asked never"
python3 - "$H2/.local/state/waiting-nudge.json" "$old" <<'EOF'
import json, sys; p, d = sys.argv[1:]; s = json.load(open(p))
for v in s.values(): v['since'] = d
json.dump(s, open(p, 'w'))
EOF
env -u WAITING_NUDGE_TODO -u WAITING_NUDGE_STATE -u WAITING_NUDGE_INBOX -u WAITING_NUDGE_TODAY HOME=$H2 "$W" --cron
eq "default: note in \$HOME/inbox, named by today" "$(ls "$H2/inbox")" "waiting-nudge-$today.md"

echo "# isolation"
eq "fake HOME never written while seams are set" "$(find "$HOME" -mindepth 1 | wc -l)" "0"
eq "real state untouched" "$(real_sum)" "$real_before"
eq "no note in the real inbox" "$(real_notes)" "$real_notes_before"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
