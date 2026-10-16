#!/usr/bin/env bash
# Tests for ~/bin/remind (one-shot behaviour + recurring --every/skip). Exit 0 = all pass.
# Everything runs against temp state: REMIND_DB, REMIND_INBOX, a stub REMIND_TG that logs instead of texting,
# and REMIND_NOW to fake the clock. The real ~/.local/state/remind/reminders.json is checked to be untouched.
set -uo pipefail
R=/home/node/bin/remind
REAL=/home/node/.local/state/remind/reminders.json
T=$(mktemp -d "${TMPDIR:-/tmp}/remind-tests.XXXXXX")
trap 'rm -rf "$T"' EXIT
export REMIND_DB=$T/rem.json REMIND_INBOX=$T/inbox REMIND_TG=$T/tg
# Fixed clock for every call (add/list/skip/cancel too, not just `due`), set before all the test dates:
# otherwise recurring adds whose start is already past on the real clock jump forward and tests flake by date.
export REMIND_NOW="2026-10-10 12:00"
cat > "$T/tg" <<STUB
#!/bin/sh
[ -e "$T/tg-fail" ] && exit 1
printf '%s\n' "\$*" >> "$T/tg.log"
STUB
chmod +x "$T/tg"
real_before=$( [ -f "$REAL" ] && stat -c '%Y %s' "$REAL"; md5sum < "$REAL" 2>/dev/null)
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2"; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else no "$1" "$2"; fi; }
reset() { rm -rf "$T/rem.json" "$T/inbox" "$T/tg.log" "$T/tg-fail"; }
ats() { python3 -c 'import json,sys; print(" ".join(r["at"] for r in json.load(open(sys.argv[1]))))' "$REMIND_DB"; }
tgn() { [ -f "$T/tg.log" ] && wc -l < "$T/tg.log" | tr -d ' ' || echo 0; }
inboxn() { ls "$T/inbox" 2>/dev/null | wc -l | tr -d ' '; }
due() { REMIND_NOW="$1" timeout 20 "$R" due; }

echo "# A. one-shot behaviour (unchanged)"
reset
out=$("$R" list); eq "empty list" "$out" "(none)"
out=$("$R" add --owner --tag bill:7 "2026-10-12 10:00" "Pay rent"); eq "add owner+tag output" "$out" "added owner reminder for Mon 2026-10-12 10:00"
out=$("$R" add "2026-10-11 08:00" "Check backups"); eq "add self output" "$out" "added reminder for Sun 2026-10-11 08:00"
"$R" add --owner "2026-10-20 09:00" "Dentist" >/dev/null
out=$("$R" list)
eq "list sorted + format" "$out" " 1. 2026-10-11 08:00  Check backups
 2. 2026-10-12 10:00  [owner] Pay rent  (tag bill:7)
 3. 2026-10-20 09:00  [owner] Dentist"
"$R" add "bad" "x" >/dev/null 2>&1; eq "bad time rejected (rc 2)" "$?" "2"
due "2026-10-11 07:59"; eq "nothing due yet" "$(tgn)/$(inboxn)" "0/0"
due "2026-10-12 10:00"
eq "due fired both due ones, left the future one" "$(ats)" "2026-10-20 09:00"
eq "owner reminder texted as ⏰ text" "$(cat "$T/tg.log")" "⏰ Pay rent"
has "self reminder became an inbox note" "$(cat "$T"/inbox/*)" "Reminder (set for 2026-10-11 08:00): Check backups"
due "2026-10-12 10:00"; eq "never fires twice" "$(tgn)/$(inboxn)" "1/1"
touch "$T/tg-fail"; due "2026-10-20 09:00"
has "tg failure falls back to inbox" "$(cat "$T"/inbox/*)" "OWNER reminder (tg failed, send it yourself) (set for 2026-10-20 09:00): Dentist"
reset
"$R" add --owner --tag bill:3 "2026-10-12 09:00" "Bill A soon" >/dev/null
"$R" add --owner --tag bill:3 "2026-10-15 09:00" "Bill A today" >/dev/null
"$R" add --owner --tag bill:4 "2026-10-15 09:00" "Bill B today" >/dev/null
out=$("$R" cancel --tag bill:3); eq "cancel --tag prints cancelled: lines (bills counts them)" "$(printf '%s\n' "$out" | grep -c '^cancelled:')" "2"
out=$("$R" cancel --tag bill:9); eq "cancel --tag no match" "$out" "no matching reminder"
out=$("$R" cancel --match "BILL b"); eq "cancel --match (case-insensitive)" "$out" "cancelled: 2026-10-15 09:00  Bill B today"
"$R" add "2026-10-13 09:00" "One" >/dev/null; "$R" add "2026-10-14 09:00" "Two" >/dev/null
out=$("$R" cancel 2); eq "cancel N" "$out" "cancelled: 2026-10-14 09:00  Two"
"$R" cancel 5 >/dev/null; eq "cancel bad N rc 1" "$?" "1"

echo "# B. recurring"
reset
out=$("$R" add --owner --every daily "2026-10-24 09:00" "Vitamins")
eq "add daily output" "$out" "added owner reminder for Sat 2026-10-24 09:00, every day"
eq "list shows (every day)" "$("$R" list)" " 1. 2026-10-24 09:00  [owner] Vitamins  (every day)"
due "2026-10-24 09:00"; eq "daily: fired, next is 10-25 09:00" "$(tgn) $(ats)" "1 2026-10-25 09:00"
due "2026-10-25 09:00"; eq "daily across DST: next 10-26 09:00 local" "$(tgn) $(ats)" "2 2026-10-26 09:00"
off=$(python3 -c 'from datetime import datetime; from zoneinfo import ZoneInfo as Z
z=Z("Europe/Zurich"); f=lambda s: datetime.strptime(s,"%Y-%m-%d %H:%M").replace(tzinfo=z)
print(f("2026-10-24 09:00").utcoffset(), f("2026-10-26 09:00").utcoffset(), (f("2026-10-25 09:00").timestamp()-f("2026-10-24 09:00").timestamp())/3600)')
eq "  (wall time kept: +2h → +1h, the DST day is 25 h long)" "$off" "2:00:00 1:00:00 25.0"
reset
"$R" add --every monthly:31 "2026-10-31 10:00" "Month end" >/dev/null
due "2026-10-31 10:00"; eq "monthly:31 → Nov 30" "$(ats)" "2026-11-30 10:00"
due "2026-11-30 10:00"; eq "monthly:31 → then Dec 31" "$(ats)" "2026-12-31 10:00"
reset
"$R" add --every monthly "2027-01-31 10:00" "Anchor" >/dev/null
due "2027-01-31 10:00"; due "2027-02-28 10:00"; eq "plain monthly from Jan 31 keeps the 31st (Feb 28 → Mar 31)" "$(ats)" "2027-03-31 10:00"
reset
"$R" add --every monthly:last "2027-01-15 10:00" "Last day" >/dev/null
eq "monthly:last snaps to Jan 31" "$(ats)" "2027-01-31 10:00"
due "2027-01-31 10:00"; eq "monthly:last → Feb 28" "$(ats)" "2027-02-28 10:00"
due "2027-02-28 10:00"; eq "monthly:last → Mar 31" "$(ats)" "2027-03-31 10:00"
reset
"$R" add --every weekdays "2026-10-16 07:30" "Standup" >/dev/null
due "2026-10-16 07:30"; eq "weekdays: Fri → Mon (skips Sat/Sun)" "$(ats)" "2026-10-19 07:30"
reset
out=$("$R" add --every weekdays "2026-10-17 07:30" "Standup")
eq "weekdays added on a Saturday snaps to Monday" "$(ats)" "2026-10-19 07:30"
has "  and says so" "$out" "(first matching day)"
reset
"$R" add --owner --every daily "2026-10-12 08:00" "Daily thing" >/dev/null
due "2026-10-15 12:00"
eq "3 days downtime: fired once, next is the next future slot" "$(tgn) $(ats)" "1 2026-10-16 08:00"
reset
"$R" add --every "mon,thu" "2026-10-12 09:00" "Gym" >/dev/null
"$R" add --every weekly:di "2026-10-13 18:00" "Dienstag" >/dev/null
"$R" add --every yearly "2026-10-28 09:00" "PersonB birthday" >/dev/null
"$R" add --every 3d "2026-10-12 21:00" "Water plants" >/dev/null
"$R" add --every 2w "2026-10-12 06:00" "Bins" >/dev/null
out=$("$R" list)
has "list (every Mon, Thu)" "$out" "Gym  (every Mon, Thu)"
has "list German day name (every Tue)" "$out" "Dienstag  (every Tue)"
has "list (every year on 28 Oct)" "$out" "(every year on 28 Oct)"
has "list (every 3 days)" "$out" "(every 3 days)"
has "list (every 2 weeks)" "$out" "(every 2 weeks)"
n=$(printf '%s\n' "$out" | grep -n 'Gym' | cut -d: -f1)
out=$("$R" skip "$n"); has "skip N: next occurrence skipped" "$out" "skipped 2026-10-12 09:00; next: Thu 2026-10-15 09:00"
eq "  series kept, one Gym entry at Thu" "$(python3 -c 'import json,sys; print([r["at"] for r in json.load(open(sys.argv[1])) if r["text"]=="Gym"])' "$REMIND_DB")" "['2026-10-15 09:00']"
n=$("$R" list | grep -n 'Water plants' | cut -d: -f1)
out=$("$R" cancel "$n"); has "cancel N cancels the series" "$out" "cancelled: 2026-10-12 21:00  Water plants  (every 3 days)"
eq "  gone for good" "$("$R" list | grep -c 'Water plants')" "0"
due "2026-10-12 21:00"; eq "3d series really gone after due" "$(grep -c 'Water' "$REMIND_DB")" "0"
"$R" add "2026-10-30 10:00" "One-shot" >/dev/null
n=$("$R" list | grep -n 'One-shot' | cut -d: -f1)
"$R" skip "$n" >/dev/null; eq "skip on a one-shot refuses (rc 1)" "$?" "1"
reset
"$R" add --every daily --until 2026-10-13 "2026-10-12 09:00" "Short series" >/dev/null
has "list shows until" "$("$R" list)" "(every day, until 2026-10-13)"
due "2026-10-12 09:00"; due "2026-10-13 09:00"; eq "--until ends the series after its last day" "$(inboxn) $(ats)" "2 "
reset
REMIND_NOW="2026-10-14 10:00" "$R" add --every daily "2026-10-10 09:00" "Past start" >/dev/null
eq "recurring with a past start moves to the next future slot" "$(ats)" "2026-10-15 09:00"
reset
"$R" add --every fortnightly "2026-10-12 09:00" "x" >/dev/null 2>&1; eq "unknown rule rejected (rc 2)" "$?" "2"
"$R" add --until 2026-10-20 "2026-10-12 09:00" "x" >/dev/null 2>&1; eq "--until without --every rejected" "$?" "2"
"$R" add --every monthly:32 "2026-10-12 09:00" "x" >/dev/null 2>&1; eq "monthly:32 rejected" "$?" "2"
"$R" add --every weekends "2026-10-12 10:00" "Weekend" >/dev/null; eq "weekends snaps to Sat" "$(ats)" "2026-10-17 10:00"
due "2026-10-17 10:00"; eq "weekends: Sat → Sun" "$(ats)" "2026-10-18 10:00"
reset
"$R" add --owner --every daily "2026-10-12 09:00" "Owner series" >/dev/null
due "2026-10-12 09:00"; eq "recurring owner reminder texted as ⏰ text" "$(cat "$T/tg.log")" "⏰ Owner series"
touch "$T/tg-fail"; due "2026-10-13 09:00"
has "recurring tg failure → inbox note with the rule" "$(cat "$T"/inbox/*)" "(set for 2026-10-13 09:00, every day): Owner series"
eq "  and the series continues" "$(ats)" "2026-10-14 09:00"

echo "# C. bills compatibility (temp bills state + temp remind db)"
reset
export BILLS_STATE_DIR=$T/bills BILLS_NOW="2026-10-10 12:00"
out=$(timeout 30 /home/node/bin/bills add --payee "Test AG" --amount 12.50 --due 2026-10-20 --source manual 2>&1)
has "bills add schedules reminders" "$out" "reminders"
eq "  two tagged owner reminders in the temp db" "$(python3 -c 'import json,sys; print(sum(1 for r in json.load(open(sys.argv[1])) if r.get("tag","").startswith("bill:") and r["owner"]))' "$REMIND_DB")" "2"
id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["tag"].split(":")[1])' "$REMIND_DB")
out=$(timeout 30 /home/node/bin/bills paid "$id" 2>&1); has "bills paid cancels them" "$out" "cancelled 2 reminders"
eq "  temp db empty" "$("$R" list)" "(none)"

real_after=$( [ -f "$REAL" ] && stat -c '%Y %s' "$REAL"; md5sum < "$REAL" 2>/dev/null)
eq "real reminders.json untouched by the tests" "$real_after" "$real_before"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
