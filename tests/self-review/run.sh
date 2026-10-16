#!/usr/bin/env bash
# Tests for ~/bin/self-review. Exit 0 = all pass. SR=<path> tests another copy (e.g. before install).
# Builds a temp brain repo (BRAIN_GIT_DIR / BRAIN_WORK_TREE) with fixtures for every check, a fake LLM
# (SELF_REVIEW_LLM) and fake hard rules. All private-looking values are FAKE and generated at run time.
# SELF_REVIEW_LIVE=1 adds one call through the real local LLM (llm-local in the vault; ~10-60 s).
# Prompt tuning on realistic fixtures (real LLM): live.sh tp (planted problems) / live.sh noise (must stay silent).
set -uo pipefail
SR=${SR:-${SELF_REVIEW_BIN:-/home/node/bin/self-review}}   # run-tests.sh passes the tool as SELF_REVIEW_BIN
T=$(mktemp -d "${TMPDIR:-/tmp}/self-review-tests.XXXXXX")
trap "${KEEP:+:} rm -rf $T" EXIT
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; }
has() { if grep -qF -- "$2" "$3"; then ok "$1"; else no "$1"; fi; }
hasnt() { if grep -qF -- "$2" "$3"; then no "$1"; else ok "$1"; fi; }
rand() { LC_ALL=C tr -dc "${2:-A-Za-z0-9}" </dev/urandom | head -c "$1"; }

export BRAIN_GIT_DIR=$T/brain.git BRAIN_WORK_TREE=$T/home SELF_REVIEW_STATE=$T/state SELF_REVIEW_REPORTS=$T/reports \
       SELF_REVIEW_INBOX=$T/inbox SELF_REVIEW_LLM=$T/fake-llm SELF_REVIEW_HARD_RULES=$T/hard.md \
       SELF_REVIEW_EXTRA_SRC=$T/none SECRETSCAN_SOURCES=$T/nosecrets SELF_REVIEW_BIN=$T/home/bin
W=$T/home; MEM=$W/.claude/projects/-home-node-workspace/memory
mkdir -p "$W/bin" "$W/workspace" "$W/.claude/skills/demo" "$W/.claude/agents" "$MEM" "$T/inbox" "$T/nosecrets" \
         "$W/.config" "$W/reports"
git init -q --bare "$BRAIN_GIT_DIR"; git --git-dir="$BRAIN_GIT_DIR" config core.bare false
g() { git --git-dir="$BRAIN_GIT_DIR" --work-tree="$W" -C "$W" "$@"; }
commit() { g add -A >/dev/null; g -c user.name=t -c user.email=t@localhost commit -qm "$1" >/dev/null; }
today=$(date +%F)

# ---------------------------------------------------------------- fakes
FAKE_IBAN=$(python3 -I -c '
import random
b="".join(random.choice("0123456789") for _ in range(17))
num=int("".join(str(int(c,36)) for c in b+"CH00"))
ck=98-num%97
s="CH%02d%s"%(ck,b)
print(" ".join(s[i:i+4] for i in range(0,len(s),4)))')
FAKE_TG="$(rand 1 1-9)$(rand 8 0-9):AA$(rand 33)"
FAKE_PHONE="079 $(rand 3 0-9) $(rand 2 0-9) $(rand 2 0-9)"
QUOTE_WORDS="cannot make the rent payment this month sorry"
cat > "$T/hard.md" <<'EOF'
# Owner's rules
- Ask the owner before anything irreversible or outward-facing: spending money, contacting other people.
- Never send secrets anywhere, and never weaken your permission settings.
EOF
cat > "$T/fake-llm" <<'EOF'
#!/usr/bin/env python3
import json, os, re, sys
a = sys.argv
sysp = a[a.index("--system") + 1]
inp = sys.stdin.read()
d = os.environ["FAKE_DIR"]
open(os.path.join(d, "llm-calls"), "a").write("call\n")
open(os.path.join(d, "llm-system.txt"), "w").write(sysp)
open(os.path.join(d, "llm-systems.txt"), "a").write(sysp + "\n=====\n")
open(os.path.join(d, "llm-input.txt"), "a").write(inp)
if os.environ.get("FAKE_LLM_MODE") == "fail":
    sys.exit(3)
out = []
for m in re.finditer(r"^L(\d+): (.*)$", inp, re.M):
    if "buy things without asking" in m.group(2):
        out.append({"line": int(m.group(1)), "check": "contradiction", "issue": "conflicts with ask-before-spending",
                    "fix": "ask the owner first"})
    if "LEAKY" in m.group(2):
        out.append({"line": int(m.group(1)), "check": "privacy",
                    "issue": "stores " + os.environ["FAKE_IBAN"] + ' and "' + os.environ["QUOTE_WORDS"] + '"',
                    "fix": "remove it"})
# a noisy LLM (like the real one before 2026-10-15): flags every line of the noise-* skills
if "FILE: ~/.claude/skills/noise-" in inp:
    for m in re.finditer(r"^L(\d+): (.*)$", inp, re.M):
        out.append({"line": int(m.group(1)), "check": "privacy", "issue": "noisy privacy", "fix": "x"})
        if "blocked" in m.group(2):
            out.append({"line": int(m.group(1)), "check": "contradiction", "issue": "noisy contradiction", "fix": "x"})
print(json.dumps({"findings": out}))
EOF
chmod +x "$T/fake-llm"
export FAKE_DIR=$T FAKE_IBAN QUOTE_WORDS
printf '#!/usr/bin/env python3\nimport argparse\np=argparse.ArgumentParser()\np.add_argument("--owner")\n' > "$W/bin/remind"
printf '#!/bin/sh\necho old\n' > "$W/bin/oldtool"
chmod +x "$W/bin/remind" "$W/bin/oldtool"

# ---------------------------------------------------------------- base snapshot
LONG_OLD="This long line explains the alpha routine in detail and has an old contact 044 $(rand 3 0-9) $(rand 2 0-9) $(rand 2 0-9) that predates the review window. It keeps going with more harmless words about the routine so that it is longer than the sentence threshold. Final sentence here."
{ echo "# How you operate"; echo; echo "$LONG_OLD"; for i in $(seq 1 30); do echo "- base rule $i: keep messages short."; done; } > "$W/workspace/CLAUDE.md"
printf -- '---\nname: demo\ndescription: demo skill\n---\nUse `remind add` for reminders.\n' > "$W/.claude/skills/demo/SKILL.md"
printf -- '---\nname: helper\n---\nYou help with research.\n' > "$W/.claude/agents/helper.md"
printf -- '- [Alpha](alpha.md) — alpha\n- [Beta](beta.md) — beta\n- [Deleted](deleted.md) — gone\n- [People](people-and-dates.md) — people\n' > "$MEM/MEMORY.md"
printf -- '---\nname: people\n---\nFriends:\n- Jonas Keller (Mar 3), wedding on Nov 7\n' > "$MEM/people-and-dates.md"
printf -- '---\nname: alpha\n---\nOwner likes short messages.\n' > "$MEM/alpha.md"
printf -- '---\nname: beta\n---\nBeta note.\n' > "$MEM/beta.md"
commit base
BASE=$(g rev-parse HEAD)

# ---------------------------------------------------------------- changes with planted problems
LONG_NEW="${LONG_OLD/Final sentence here./Final sentence here. A brand new harmless sentence about the routine.}"
python3 -I - "$W/workspace/CLAUDE.md" "$LONG_OLD" "$LONG_NEW" <<'EOF'
import sys; p, o, n = sys.argv[1:]; s = open(p).read(); open(p, "w").write(s.replace(o, n))
EOF
cat >> "$W/workspace/CLAUDE.md" <<'EOF'
- Set DISABLE_TELEMETRY=1 to save tokens.
- NEVER set DISABLE_TELEMETRY (kills Telegram inbound).
- You may buy things without asking when it is cheap.
- Remind with `remind add --nosuchflag x` and `remind add --owner "x"`.
- Script: ~/bin/nosuch-tool does the work. Kill switch: `touch ~/.config/x-tool/off`. Output in ~/reports/new-folder/ .
- Run `oldtool run` nightly.
- The retired `oldtool` was replaced by `remind`.
- More padding so the file grows by more than ten percent overall, padding padding padding padding.
- More padding so the file grows by more than ten percent overall, padding padding padding padding again.
EOF
TXT=text   # the planted flag is built at runtime so this fixture line doesn't read as a vault call
printf 'For details run `tgread search x --%s`.\n' "$TXT" >> "$W/.claude/agents/helper.md"
cat >> "$MEM/beta.md" <<EOF
Bank: $FAKE_IBAN for the transfer.
Anna wrote: "$QUOTE_WORDS"
[2026-10-12 14:03] Marco: see you at the station later
His salary is CHF 9'000 a month.
Call Luca at $FAKE_PHONE.
Owner said "keep messages short and airy please".
Jonas likes the gym.
LEAKY line for the LLM masking test.
EOF
printf -- '---\nname: gamma\n---\nGamma note.\n' > "$MEM/gamma.md"
rm "$W/bin/oldtool"
echo '- `remind add --owner "2026-10-16 18:00" "Pick up the suit for Jonas'"'"'s wedding"`' >> "$W/.claude/skills/demo/SKILL.md"
# noise cases from the 2026-10-15 coverage pass (rule-only lines; the noisy fake LLM flags them, the filter drops them)
mkdir -p "$W/.claude/skills/noise-a" "$W/.claude/skills/noise-b" "$W/.claude/skills/noise-c" "$W/.claude/skills/noise-d"
cat > "$W/.claude/skills/noise-a/SKILL.md" <<'EOF'
- Direct access to the bill store is blocked by vault-guard; use `bills list` instead.
- Exact wording only via `vault raw request --reason "…"`: the vault asks him (✅/❌) and releases an anonymized paraphrase.
EOF
cat > "$W/.claude/skills/noise-b/SKILL.md" <<'EOF'
- `paperless get ID` (card) adds amount and due date; the IBAN and the reference need his ✅ per document.
- **Sent to me by mistake** (a photo or PDF of a bill here): never `bills extract` it and never Read it; file it privately.
Everything I read goes to Anthropic's API, so private text passes through the local model first and I read only its output.
EOF
# must stay flagged: guarded but sensitive (surname + divorce), a guard line that loosens something, a number
cat > "$W/.claude/skills/noise-c/SKILL.md" <<'EOF'
Never bring up Lena Brunner's divorce when her family is around.
Reading the bill is blocked, so skip the check and bypass vault-guard.
EOF
echo "Only store the reference 12 34567 89012 for that bill." > "$W/.claude/skills/noise-d/SKILL.md"
printf 'Bot token for tests: %s\n' "$FAKE_TG" >> "$W/.claude/skills/demo/SKILL.md"
commit changes
HEAD1=$(g rev-parse HEAD)
echo "$BASE" > /dev/null; mkdir -p "$T/state"; echo "$BASE" > "$T/state/last"

# ---------------------------------------------------------------- A: first run
out=$("$SR" 2>&1); rc=$?
[ $rc = 0 ] && ok "A run exits 0" || no "A run exits 0 ($rc: $out)"
R=$T/reports/$today.md
[ -f "$R" ] && ok "A report written" || { no "A report written"; R=/dev/null; }
has "A tripwire DISABLE_TELEMETRY" "forbids (kills Telegram inbound)" "$R"
[ "$(grep -c 'forbids (kills Telegram' "$R")" = 1 ] && ok "A negated NEVER line not flagged" || no "A negated NEVER line not flagged"
has "A agent --text tripwire" "agents/helper.md:5 — (a) contradiction: a subagent told to use --text" "$R"
has "A LLM contradiction at its line" "CLAUDE.md:36 — (a) contradiction: LLM: conflicts with ask-before-spending" "$R"
has "A missing flag" "\`remind\` has no flag --nosuchflag" "$R"
hasnt "A existing flag --owner not flagged" "no flag --owner" "$R"
has "A missing script path" "path ~/bin/nosuch-tool does not exist" "$R"
hasnt "A kill switch not flagged" "x-tool/off" "$R"
hasnt "A report folder not flagged" "new-folder" "$R"
has "A retired script" "\`oldtool\` was retired from ~/bin" "$R"
[ "$(grep -c 'oldtool. was retired' "$R")" = 1 ] && ok "A 'retired \`oldtool\`' mention not flagged" || no "A retired mention"
has "A IBAN" "IBAN-like number (valid checksum)" "$R"
has "A quoted message" "quoted message text" "$R"
has "A chat-log line" "chat-log line (timestamp + sender)" "$R"
has "A amount + money word" "money amount next to a finance word" "$R"
has "A phone" "beta.md:9 — (c) privacy: phone number" "$R"
[ "$(grep -c 'privacy: phone number' "$R")" = 1 ] && ok "A unchanged sentence of a long line not checked" || no "A unchanged sentence checked"
hasnt "A owner's own quote not flagged" "beta.md:10 —" "$R"
has "A secretscan hit (name only)" "secretscan hit: pattern:telegram-bot-token" "$R"
has "A index: unindexed note" "gamma.md:1 — (d) index drift: memory note missing from the MEMORY.md index" "$R"
has "A index: missing file" "index line points to missing note deleted.md" "$R"
has "A growth" "(e) CLAUDE.md growth: CLAUDE.md grew" "$R"
has "A LLM masked IBAN" "LLM: stores [IBAN] and [quote]" "$R"
for v in "$FAKE_IBAN" "$QUOTE_WORDS" "$FAKE_PHONE" "$FAKE_TG" "see you at the station" "9'000"; do
  hasnt "A report never holds planted value (${v:0:6}…)" "$v" "$R"
done
grep -q "Never send secrets anywhere" "$T/llm-system.txt" && ok "A LLM gets the hard rules" || no "A LLM gets the hard rules"
grep -q "A brand new harmless sentence" "$T/llm-input.txt" && ok "A LLM gets the changed sentence" || no "A changed sentence"
grep -q "that predates the review window" "$T/llm-input.txt" && no "A LLM got an unchanged sentence" || ok "A LLM gets only changed sentences"
n=$(ls "$T/inbox" | wc -l); [ "$n" = 1 ] && ok "A one inbox note" || no "A one inbox note ($n)"
N=$(ls "$T"/inbox/self-review-*.md 2>/dev/null | head -1)
has "A note lists findings with file:line" "agents/helper.md:5" "${N:-/dev/null}"
for v in "$FAKE_IBAN" "$QUOTE_WORDS" "$FAKE_TG"; do hasnt "A note never holds planted value (${v:0:6}…)" "$v" "${N:-/dev/null}"; done
[ "$(cat "$T/state/last")" = "$HEAD1" ] && ok "A state last = HEAD" || no "A state last = HEAD"
nopen=$("$SR" open --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "$nopen" -ge 15 ] && ok "A open findings stored ($nopen)" || no "A open findings stored ($nopen)"

hasnt "A noise: guard/approval lines silent (noise-a)" "skills/noise-a/" "$R"
hasnt "A noise: tier-1 fields, don't-Read, premise silent (noise-b)" "skills/noise-b/" "$R"
has "A noise: 6 LLM findings dropped and counted" "6 findings dropped (rule-only lines)" "$R"
has "A noise filter keeps a guarded line with a surname + sensitive topic" "noise-c/SKILL.md:1 — (c) privacy: LLM: noisy privacy" "$R"
has "A noise filter keeps a guard line that loosens something" "noise-c/SKILL.md:2 — (a) contradiction: LLM: noisy contradiction" "$R"
has "A noise filter keeps a line with a number" "noise-d/SKILL.md:1 — (c) privacy: LLM: noisy privacy" "$R"
dl=$(grep -n "Jonas" "$W/.claude/skills/demo/SKILL.md" | cut -d: -f1)
has "A people-memory name in a skill example" "skills/demo/SKILL.md:$dl — (c) privacy: names a person from the people memory" "$R"
hasnt "A people-memory name in a memory note not flagged" "beta.md:11 — (c) privacy: names a person" "$R"
hasnt "A report never holds the person's name" "Jonas" "$R"
grep -q "NOT a finding" "$T/llm-system.txt" && ok "A prompt has the not-a-finding guidance" || no "A prompt not-a-finding guidance"
grep -q "server name and IP" "$T/llm-system.txt" && grep -q "Krankenkasse" "$T/llm-system.txt" && grep -q "find the mail from X" "$T/llm-system.txt" \
  && ok "A prompt covers owner data, search terms, trigger phrases" || no "A prompt covers owner data, search terms, trigger phrases"
grep -q "skills/noise-a/SKILL.md (skill: generic docs" "$T/llm-input.txt" && ok "A LLM gets the file kind" || no "A LLM gets the file kind"
grep -q "^- You may buy things without asking" "$T/llm-systems.txt" && no "A changed rule line fed back as a RULE" || ok "A changed rule line not fed back as a RULE"

# ---------------------------------------------------------------- B: no changes -> nothing new
"$SR" --cron >/dev/null 2>&1
n=$(ls "$T/inbox" | wc -l); [ "$n" = 1 ] && ok "B no new note without new findings" || no "B no new note ($n)"
has "B report lists earlier open findings" "Still open from earlier" "$T/reports/$today.md"

# ---------------------------------------------------------------- C: fixing a line resolves its finding
sed -i "/^Bank: /d" "$MEM/beta.md"; commit fix-iban
"$SR" --cron >/dev/null 2>&1
"$SR" open > "$T/open.txt"
hasnt "C fixed IBAN line no longer open" "IBAN-like" "$T/open.txt"
has "C other findings still open" "phone number" "$T/open.txt"
n=$(ls "$T/inbox" | wc -l); [ "$n" = 1 ] && ok "C no note for a fix" || no "C no note for a fix ($n)"

# ---------------------------------------------------------------- D: ack
id=$(grep -- "helper.md:5" "$T/open.txt" | head -1 | sed -E 's/^- `([0-9a-f]+)`.*/\1/')
"$SR" ack "$id" >/dev/null
"$SR" open > "$T/open2.txt"
hasnt "D acked finding hidden" "helper.md:5" "$T/open2.txt"

# ---------------------------------------------------------------- E: --dry-run writes nothing
before=$(find "$T/reports" "$T/inbox" "$T/state" -type f -newer "$T/open2.txt" | wc -l)
sleep 1; touch "$T/mark"
"$SR" --since "$BASE" --dry-run > "$T/dry.txt" 2>&1
after=$(find "$T/reports" "$T/inbox" "$T/state" -type f -newer "$T/mark" | wc -l)
[ "$after" = 0 ] && ok "E dry run writes nothing" || no "E dry run wrote $after files"
has "E dry run prints the report" "# Self-review" "$T/dry.txt"

# ---------------------------------------------------------------- F: LLM failure -> skipped and noted
last_before=$(cat "$T/state/last")
FAKE_LLM_MODE=fail "$SR" --since "$BASE" --report-only > "$T/f.txt" 2>&1
RF=$(ls -t "$T"/reports/*.md | head -1)
has "F LLM failure noted" "LLM unavailable" "$RF"
has "F deterministic findings still there" "path ~/bin/nosuch-tool does not exist" "$RF"
[ "$(cat "$T/state/last")" = "$last_before" ] && ok "F report-only keeps state" || no "F report-only keeps state"
n=$(ls "$T/inbox" | wc -l); [ "$n" = 1 ] && ok "F report-only writes no note" || no "F report-only note ($n)"

# ---------------------------------------------------------------- G: budget + --no-llm + date --since
: > "$T/llm-calls"
"$SR" --since "$BASE" --dry-run --max-llm 1 > "$T/g.txt" 2>&1
[ "$(wc -l < "$T/llm-calls")" = 1 ] && ok "G --max-llm caps calls" || no "G --max-llm caps calls"
has "G skipped calls counted" "skipped (budget)" "$T/g.txt"
: > "$T/llm-calls"
"$SR" --since "$BASE" --dry-run --no-llm > /dev/null 2>&1
[ ! -s "$T/llm-calls" ] && ok "G --no-llm makes no calls" || no "G --no-llm"
"$SR" --since "30 days ago" --dry-run --no-llm > "$T/g2.txt" 2>&1
has "G date before the repo start = first snapshot" "Range: ${BASE:0:7}.." "$T/g2.txt"
"$SR" --since "not-a-rev-or-date" --dry-run >/dev/null 2>&1 && no "G bad --since rejected" || ok "G bad --since rejected"

# ---------------------------------------------------------------- H: live local LLM (optional)
if [ "${SELF_REVIEW_LIVE:-0}" = 1 ]; then
  SECONDS=0
  SELF_REVIEW_LLM=/home/node/bin/llm-local "$SR" --since "$BASE" --dry-run --max-llm 1 > "$T/live.txt" 2>&1
  if grep -q "LLM: 1 calls ok" "$T/live.txt"; then ok "H live LLM call ok (${SECONDS}s)"; else
    no "H live LLM call ($(grep -m1 'LLM' "$T/live.txt"))"; fi
  grep -q "LLM:.*ask\|LLM:.*spend\|LLM:.*permission\|LLM:.*owner" "$T/live.txt" && echo "info - live LLM flagged the planted contradiction" \
    || echo "info - live LLM did not flag the planted contradiction"
fi

echo "---"; echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
