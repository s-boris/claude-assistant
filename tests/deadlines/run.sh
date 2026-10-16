#!/usr/bin/env bash
# Tests for ~/bin/deadlines. Exit 0 = all pass.
# Everything runs on temp state: REMIND_DB/REMIND_INBOX/REMIND_TG for remind, DEADLINES_STATE for the sync
# ledger, DEADLINES_NOW to fake the clock, DEADLINES_PAPERLESS -> a fake paperless (fixture docs only).
# The real reminders file and the real deadlines ledger are checked to be untouched.
set -uo pipefail
# Synthetic profile (tests never read the real one): repo layout tests/profile, live layout ~/opt/profile-tests.
_pf="$(cd "$(dirname "$0")/.." && pwd)"; [ -f "$_pf/profile/fixtures/profile.json" ] && _pf=$_pf/profile || _pf=$_pf/profile-tests
export ASSISTANT_PROFILE="$_pf/fixtures/profile.json"
D=${DEADLINES_BIN:-/home/node/bin/deadlines}   # DEADLINES_BIN: test a copy
R=/home/node/bin/remind
HERE=$(cd "$(dirname "$0")" && pwd)
REAL_REM=/home/node/.local/state/remind/reminders.json
REAL_LEDGER=/home/node/.local/state/deadlines/synced.json
T=$(mktemp -d "${TMPDIR:-/tmp}/deadlines-tests.XXXXXX")
trap 'rm -rf "$T"' EXIT
export REMIND_DB=$T/rem.json REMIND_INBOX=$T/inbox REMIND_TG=$T/tg DEADLINES_STATE=$T/ledger.json
export DEADLINES_PAPERLESS=$HERE/fake_paperless.py FAKE_PAPERLESS_LOG=$T/paperless.log
export DEADLINES_PROFILE=$T/profile.json
unset REMIND_NOW DEADLINES_TODAY DEADLINES_DATA
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> %s/tg.log\n' "$T" > "$T/tg"; chmod +x "$T/tg"
live=1; for f in /home/node/.config/deadlines/ch-xx.json /home/node/.config/deadlines/profile.json; do [ -f "$f" ] || live=0; done
[ $live = 1 ] && cp /home/node/.config/deadlines/profile.json "$T/profile.json"
fp() { [ -f "$1" ] && md5sum < "$1" || echo absent; }
real_rem_before=$(fp "$REAL_REM"); real_ledger_before=$(fp "$REAL_LEDGER")
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2"; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else no "$1" "$2"; fi; }
hasnt() { if printf '%s' "$2" | grep -qF -- "$3"; then no "$1" "$2"; else ok "$1"; fi; }
nrem() { python3 -c 'import json,sys
try: print(len(json.load(open(sys.argv[1]))))
except FileNotFoundError: print(0)' "$REMIND_DB"; }
tags() { python3 -c 'import json,sys
try: print(" ".join(sorted(r.get("tag","") for r in json.load(open(sys.argv[1])))))
except FileNotFoundError: print("")' "$REMIND_DB"; }
reset() { rm -f "$REMIND_DB" "$DEADLINES_STATE" "$T/tg.log"; rm -rf "$REMIND_INBOX"; cp /home/node/.config/deadlines/profile.json "$T/profile.json"; }
setprof() { python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); v=sys.argv[3]; d[sys.argv[2]]=None if v=="null" else (True if v=="true" else v); json.dump(d,open(p,"w"))' "$T/profile.json" "$1" "$2"; }
py() { python3 -I -c "import importlib.util,sys; from importlib.machinery import SourceFileLoader as L; m=L('dl','$D').load_module(); $1"; }

echo "# A. data file and dates"
if [ $live = 1 ]; then
out=$(timeout 30 "$D" validate); rc=$?
eq "real data file validates (rc 0)" "$rc" "0"
has "validate summary" "$out" "OK"
fi
eq "data file follows home.canton (fixture ZZ)" "$(DEADLINES_DATA= py 'print(m.CANTON, m.DATA.endswith("/.config/deadlines/ch-zz.json"))')" "ZZ True"
eq "Easter 2026" "$(py 'print(m.easter(2026))')" "2026-04-05"
eq "Easter 2027" "$(py 'print(m.easter(2027))')" "2027-03-28"
eq "XX holidays 2027 include Good Friday, Easter Monday, Ascension, Whit Monday, Berchtoldstag" \
   "$(py 'from datetime import date; h=m.holidays_sh(2027); print(all(date(2027,*x) in h for x in [(3,26),(3,29),(5,6),(5,17),(1,2),(5,1),(8,1)]))')" "True"
eq "act-by 'before': Sun 30 Nov 2025 -> Fri 28 Nov" "$(py 'from datetime import date; print(m.shift(date(2025,11,30),"before"))')" "2025-11-28"
eq "legal 'after': Sat 31 Mar 2029 -> Tue 3 Apr (Easter Monday 2 Apr)" "$(py 'from datetime import date; print(m.shift(date(2029,3,31),"after"))')" "2029-04-03"
eq "'before' over Christmas: Sat 26 Dec 2026 -> Thu 24 Dec" "$(py 'from datetime import date; print(m.shift(date(2026,12,26),"before"))')" "2026-12-24"
eq "add_months clamps: 31 Mar 2027 - 1 -> 28 Feb" "$(py 'from datetime import date; print(m.add_months(date(2027,3,31),-1))')" "2027-02-28"
eq "add_months across the year: 14 Jan - 1 -> 14 Dec" "$(py 'from datetime import date; print(m.add_months(date(2027,1,14),-1))')" "2026-12-14"
if [ $live = 0 ]; then
  echo "skip - validate, list/show, sync and checklist: no /home/node/.config/deadlines/ch-xx.json + profile.json (curated data file and owner profile; not part of a checkout)"
  echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]; exit
fi
bad=$T/bad.json
python3 -c 'import json; d=json.load(open("/home/node/.config/deadlines/ch-xx.json")); d["deadlines"].append(dict(d["deadlines"][0])); d["deadlines"].append({"id":"x","title":"X","date":"13-40"}); json.dump(d,open("'"$bad"'","w"))'
out=$(DEADLINES_DATA=$bad timeout 30 "$D" validate); rc=$?
eq "broken data file fails validation (rc 1)" "$rc" "1"
has "duplicate id reported" "$out" "duplicate deadline ids"
has "impossible date reported" "$out" "x: impossible date"
noamt=$T/noamt.json
python3 -c 'import json; d=json.load(open("/home/node/.config/deadlines/ch-xx.json")); [it["amounts"].pop("2027") for it in d["deadlines"] if it["id"]=="pillar3a"]; json.dump(d,open("'"$noamt"'","w"))'
out=$(DEADLINES_DATA=$noamt DEADLINES_TODAY=2026-10-10 timeout 30 "$D" validate)
has "missing next-year amount warns" "$out" "pillar3a: no amount for 2027"
out=$(DEADLINES_DATA=$noamt DEADLINES_TODAY=2027-01-05 timeout 30 "$D" list --days 400)
has "missing amount shows a visible placeholder, not a crash" "$out" "(amount for 2027 not in data yet)"

echo "# B. list / show"
out=$(DEADLINES_TODAY=2026-10-10 timeout 30 "$D" list)
first=$(printf '%s\n' "$out" | sed -n 2p)
has "first upcoming is the KK switch (30 Nov)" "$first" "Mon 30 Nov 2026"
has "KK switch marked as handled by kk-compare" "$out" "→ kk-compare"
has "3a 2026 shows CHF 7,258" "$out" "Säule 3a 2026: pay in the maximum (CHF 7,258)"
has "tax return 2026 due Wed 31 Mar 2027" "$out" "Wed 31 Mar 2027  XX tax return 2026 due"
has "instalment 3 on Tue 01 Dec 2026" "$out" "Tue 01 Dec 2026  XX/Gemeinde taxes 2026: 3rd provisional instalment due"
hasnt "profile-unknown Halbtax hidden without --all" "$out" "Halbtax"
order=$(DEADLINES_TODAY=2026-10-10 timeout 30 "$D" list --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(all(d[i]["act_by"]<=d[i+1]["act_by"] for i in range(len(d)-1)), len(d)>5)')
eq "list --json sorted by act-by date" "$order" "True True"
out=$(DEADLINES_TODAY=2026-10-10 timeout 30 "$D" list --all)
has "--all lists unknown profile dates" "$out" "Halbtax renews automatically: last day to cancel  [profile \"halbtax_expiry\"]"
out=$(DEADLINES_TODAY=2027-01-02 timeout 30 "$D" list --days 400)
has "in 2027 the 3a item shows the 2027 max CHF 7,373" "$out" "Säule 3a 2027: pay in the maximum (CHF 7,373)"
setprof halbtax_expiry 2027-03-14
out=$(DEADLINES_TODAY=2026-10-10 timeout 30 "$D" list)
has "Halbtax from profile: cancel 1 month before expiry (14 Mar -> Sun 14 Feb 2027)" "$out" "Sun 14 Feb 2027  Halbtax renews automatically"
setprof w8ben_signed 2024-05-20
out=$(DEADLINES_TODAY=2026-10-10 timeout 30 "$D" list --days 500)
has "W-8BEN signed 2024 expires 31 Dec 2027" "$out" "Fri 31 Dec 2027  W-8BEN at your foreign broker expires"
reset
out=$(timeout 30 "$D" show pillar3a)
has "show lists sources" "$out" "source: BSV"
has "show lists sync leads" "$out" "sync reminders: 30 d before, 14 d before"
timeout 30 "$D" show nope >/dev/null 2>&1; eq "show unknown id fails" "$?" "1"

echo "# C. sync (temp remind DB only)"
reset
export DEADLINES_NOW="2026-10-10 10:00"
out=$(timeout 60 "$D" sync --dry-run)
has "dry run reports planned reminders" "$out" "dry run: "
eq "dry run creates nothing" "$(nrem)" "0"
[ -f "$DEADLINES_STATE" ] && no "dry run wrote the ledger" || ok "dry run wrote no ledger"
out=$(timeout 60 "$D" sync); rc=$?
eq "sync rc 0" "$rc" "0"
n1=$(nrem)
eq "sync created 8 reminders (default horizon 200 d)" "$n1" "8"
eq "all tags start with deadline:" "$(tags | tr ' ' '\n' | grep -vc '^deadline:')" "0"
all_owner=$(python3 -c 'import json,sys; print(all(r["owner"] for r in json.load(open(sys.argv[1]))))' "$REMIND_DB")
eq "all are owner reminders" "$all_owner" "True"
maxlen=$(python3 -c 'import json,sys; print(max(len(r["text"]) for r in json.load(open(sys.argv[1]))))' "$REMIND_DB")
[ "$maxlen" -le 300 ] && ok "reminder texts are short (max $maxlen chars)" || no "reminder text too long" "$maxlen"
has "3a reminder 30 days before 31 Dec" "$(tags)" "deadline:pillar3a:2026-12-31:30d"
hasnt "covered KK switch not synced" "$(tags)" "kk-switch"
hasnt "informational federal-tax item not synced" "$(tags)" "dbst-due"
hasnt "profile-unknown items not synced" "$(tags)" "halbtax"
txt=$(python3 -c 'import json,sys; print([r["text"] for r in json.load(open(sys.argv[1])) if r["tag"]=="deadline:pillar3a:2026-12-31:30d"][0])' "$REMIND_DB")
has "3a reminder text has the amount and date" "$txt" "CHF 7,258"
has "3a reminder text has the act-by date" "$txt" "Thu 31 Dec (in 30 days)"
out=$(timeout 60 "$D" sync)
has "second sync changes nothing (idempotent)" "$out" "0 added, 0 updated, 0 removed, 8 already set"
eq "still 8" "$(nrem)" "8"
# the first reminder fires (28 Nov): remind due drops it; a later sync must NOT re-create it
REMIND_NOW="2026-11-28 09:00" timeout 30 "$R" due
eq "remind due fired one owner reminder" "$(wc -l < "$T/tg.log" | tr -d ' ')" "1"
out=$(DEADLINES_NOW="2026-11-28 09:05" timeout 60 "$D" sync --horizon 100)
hasnt "fired reminder is not re-created" "$(tags)" "deadline:tax-rate-3:2026-12-01:3d"
hasnt "no catch-up nudge for a lead that already fired" "$(tags)" "deadline:tax-rate-3:2026-12-01:late"
# the owner cancels one: not resurrected, --force brings it back
timeout 30 "$R" cancel --tag deadline:kk-franchise-up:2026-12-31:17d >/dev/null
out=$(timeout 60 "$D" sync)
hasnt "owner-cancelled reminder stays cancelled" "$(tags)" "deadline:kk-franchise-up:2026-12-31:17d"
has "sync reports it as skipped" "$out" "skipped (fired or cancelled earlier"
out=$(timeout 60 "$D" sync --force)
has "re-created tag present" "$(tags)" "deadline:kk-franchise-up:2026-12-31:17d"
# data change: the 3a lead moves from 14 to 10 days -> old reminder removed, new one added
moved=$T/moved.json
python3 -c 'import json; d=json.load(open("/home/node/.config/deadlines/ch-xx.json")); [it.update(remind=[{"before":30},{"before":10}]) for it in d["deadlines"] if it["id"]=="pillar3a"]; json.dump(d,open("'"$moved"'","w"))'
out=$(DEADLINES_DATA=$moved timeout 60 "$D" sync)
has "changed lead: new reminder added" "$out" "deadline:pillar3a:2026-12-31:10d"
has "changed lead: stale reminder removed" "$out" "removed"
hasnt "old 14d tag gone" "$(tags)" "deadline:pillar3a:2026-12-31:14d"
# text change -> updated in place
retext=$T/retext.json
python3 -c 'import json; d=json.load(open("/home/node/.config/deadlines/ch-xx.json")); [it.update(remind_text="NEW TEXT") for it in d["deadlines"] if it["id"]=="tax-return"]; json.dump(d,open("'"$retext"'","w"))'
out=$(DEADLINES_DATA=$retext timeout 60 "$D" sync)
has "changed text: reminders updated" "$out" "2 updated"
has "updated text stored" "$(cat "$REMIND_DB")" "NEW TEXT"
# a profile flag switches an item on
setprof halbtax_expiry 2027-03-14
out=$(DEADLINES_DATA=$retext timeout 60 "$D" sync)
has "profile date set: Halbtax reminder added 7 d before the cancel date" "$(tags)" "deadline:halbtax:2027-02-14:7d"
setprof halbtax_expiry null
out=$(DEADLINES_DATA=$retext timeout 60 "$D" sync)
hasnt "profile cleared: Halbtax reminder removed again" "$(tags)" "deadline:halbtax"
# reminders beyond the horizon are left alone by a shorter-horizon run
out=$(DEADLINES_DATA=$retext timeout 60 "$D" sync --horizon 30)
has "short horizon keeps far reminders" "$(tags)" "deadline:tax-return:2027-03-31:7d"
# non-deadline reminders are never touched
timeout 30 "$R" add --owner --tag bill:9 "2026-12-20 09:00" "Pay rent" >/dev/null
out=$(DEADLINES_DATA=$retext timeout 60 "$D" sync)
has "foreign (bill:) reminder untouched" "$(tags)" "bill:9"
# late catch-up: all leads already past, deadline still ahead -> one nudge at the next 09:00
reset
out=$(DEADLINES_NOW="2026-12-28 15:00" timeout 60 "$D" sync --horizon 10)
has "late catch-up for 3a (leads past, deadline 31 Dec)" "$(tags)" "deadline:pillar3a:2026-12-31:late"
at=$(python3 -c 'import json,sys; print([r["at"] for r in json.load(open(sys.argv[1])) if r["tag"].endswith(":late") and "pillar3a" in r["tag"]][0])' "$REMIND_DB")
eq "catch-up scheduled next morning 09:00" "$at" "2026-12-29 09:00"
REMIND_NOW="2026-12-29 09:00" timeout 30 "$R" due
out=$(DEADLINES_NOW="2026-12-29 10:00" timeout 60 "$D" sync --horizon 10)
hasnt "catch-up not repeated after it fired" "$(tags)" "pillar3a:2026-12-31:late"
unset DEADLINES_NOW

echo "# D. checklist (fake paperless)"
reset
: > "$FAKE_PAPERLESS_LOG"
out=$(DEADLINES_TODAY=2027-02-25 timeout 60 "$D" checklist tax 2026); rc=$?
eq "checklist rc 0" "$rc" "0"
has "title for tax year 2026" "$out" "XX tax return 2026: documents to collect"
has "Lohnausweis 2026 found by its period" "$out" "[x] Lohnausweis 2026"
has "evidence names the doc" "$out" "#101"
hasnt "2025 Lohnausweis not counted for 2026" "$out" "#102"
has "3a certificate found (mentions 31.12.2026)" "$out" "[x] Säule 3a certificate 2026"
hasnt "payslip excluded from the 3a item by type" "$out" "#401"
has "Zinsausweis without 31.12 is only a maybe" "$out" "[?] Bank and Swiss broker tax statements 2026"
has "if-applicable item labelled" "$out" "(if applicable: profile \"foreign_broker\" unknown)"
has "optional item labelled" "$out" "(optional)"
has "summary counts" "$out" "2/11 found in paperless"
bad_calls=$(grep -cvE '^(search|fields) ' "$FAKE_PAPERLESS_LOG")
eq "paperless used read-only (only search/fields)" "$bad_calls" "0"
eq "no content or card reads (plan B)" "$(grep -c '^get ' "$FAKE_PAPERLESS_LOG")" "0"
eq "tier-1 fields only" "$(grep '^fields ' "$FAKE_PAPERLESS_LOG" | grep -cE 'iban|reference|dates|number|address')" "0"
has "Lohnausweis evidence is the period years" "$out" "period 2026"
has "3a evidence: dated by its period years" "$out" "dated 2026"
out=$(DEADLINES_TODAY=2027-02-25 timeout 60 "$D" checklist tax 2024)
has "2024: two employers found" "$out" "#103"
has "2024: second employer too" "$out" "#104"
out=$(DEADLINES_TODAY=2027-02-25 timeout 60 "$D" checklist tax 2025)
has "2025: a birth year (19xx) before the period doesn't block found" "$out" "#105"
has "2025: Lohnausweis found" "$out" "[x] Lohnausweis 2025"
json=$(DEADLINES_TODAY=2027-02-25 timeout 60 "$D" checklist tax 2026 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); s={i["id"]:i["status"] for i in d["items"]}; print(d["tax_year"], s["lohnausweis"], s["pillar3a-cert"], s["bank-statements"], s["tax-form"])')
eq "--json statuses" "$json" "2026 found found maybe n/a"
out=$(FAKE_PAPERLESS_FAIL=1 timeout 60 "$D" checklist tax 2026); rc=$?
eq "paperless down: still rc 0" "$rc" "0"
has "paperless down: error shown" "$out" "paperless unavailable"
calls=$(FAKE_PAPERLESS_LOG=$T/p2.log FAKE_PAPERLESS_FAIL=1 timeout 60 "$D" checklist tax 2026 >/dev/null; wc -l < "$T/p2.log" | tr -d ' ')
eq "paperless down: gives up after the first failure" "$calls" "1"
out=$(timeout 60 "$D" checklist tax 2026 --offline)
has "--offline skips paperless" "$out" "offline: paperless not checked"
out=$(DEADLINES_TODAY=2027-02-25 timeout 60 "$D" checklist tax 2026 --md)
has "checklist --md: table header" "$out" "| Status | Document | From · when | Evidence |"
has "checklist --md: found row" "$out" "| have (paperless) | Lohnausweis 2026"
timeout 30 "$D" checklist nope 2026 >/dev/null 2>&1; eq "unknown checklist fails" "$?" "1"

echo "# E. real state untouched"
eq "real reminders file unchanged" "$(fp "$REAL_REM")" "$real_rem_before"
eq "real deadlines ledger unchanged" "$(fp "$REAL_LEDGER")" "$real_ledger_before"

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
