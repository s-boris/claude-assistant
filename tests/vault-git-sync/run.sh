#!/usr/bin/env bash
# Offline tests for ~/bin/vault-git-sync (commit + push ~/opt/claude-vault to its private GitHub repo; cron 03:41 with
# CRON=1, and after `vault admin deploy`/release). Exit 0 = all pass.
# Real git on a temp clone whose origin is a local bare repo; the seams VAULT_GIT_DIR / VAULT_GIT_SRC_BIN /
# VAULT_GIT_SRC_OPT point at fixtures. A `git` PATH shim refuses to run outside the temp dir and refuses any push to a
# non-local remote, so neither the real vault repo nor GitHub can be reached even if a seam breaks. HOME is a temp dir
# (~/inbox notes land there). secretscan is the real one (offline), behind a logging shim.
# Covers: vendoring (cp -p), test mirroring (--delete, excludes), default/custom message, nothing to do (no push),
# ahead of upstream (retry push), missing/unreadable source (one ~/inbox note per source, deduped, push skipped,
# held-back commit pushed once it is back), secretscan flag (nothing committed, index reset, note only with CRON=1,
# no secret in output or note), push failure + retry, commit failure, missing repo, public-path gate (block,
# pass, ahead, cannot run, empty/no config), defaults, isolation.
# VAULT_GIT_SYNC_BIN=<path> tests another copy.
# Synthetic profile (tests never read the real one): repo layout tests/profile, live layout ~/opt/profile-tests.
_pf="$(cd "$(dirname "$0")/.." && pwd)"; [ -f "$_pf/profile/fixtures/profile.json" ] && _pf=$_pf/profile || _pf=$_pf/profile-tests
export ASSISTANT_PROFILE="$_pf/fixtures/profile.json"
set -uo pipefail
V=${VAULT_GIT_SYNC_BIN:-/home/node/bin/vault-git-sync}
# a copy without the seams would cp/rsync into the real vault repo before the git shim could stop it
grep -q 'VAULT_GIT_DIR' "$V" && grep -q 'VAULT_GIT_SRC_OPT' "$V" || { echo "refusing: $V has no VAULT_GIT_DIR/VAULT_GIT_SRC_* seams"; exit 2; }
REAL_REPO=/home/node/opt/claude-vault
REAL_INBOX=/home/node/inbox
export REAL_GIT=$(command -v git) REAL_SECRETSCAN=/home/node/bin/secretscan
T=$(mktemp -d "${TMPDIR:-/tmp}/vault-git-sync-tests.XXXXXX")
trap 'rm -rf "$T"' EXIT
export T FAKE=$T/fake HOME=$T/home GIT_CONFIG_NOSYSTEM=1
export VAULT_GIT_DIR=$T/repo VAULT_GIT_SRC_BIN=$T/src/bin VAULT_GIT_SRC_OPT=$T/src/opt
unset CRON
mkdir -p "$T/shims" "$FAKE" "$HOME/inbox"
rgit() { "$REAL_GIT" --no-optional-locks -C "$REAL_REPO" "$@" 2>/dev/null; }
real_state() { echo "$(rgit rev-parse HEAD) $(rgit status --porcelain | sha256sum | cut -c1-16)"; }
real_notes() { ls "$REAL_INBOX" 2>/dev/null | grep -c '^vault-git-sync-'; }
real_before=$(real_state); real_notes_before=$(real_notes)

# ---- shims
mk() { printf '#!/bin/bash\n%s\n' "$2" > "$T/shims/$1"; chmod +x "$T/shims/$1"; }
mk git 'echo "git $*" >> "$FAKE/git.log"
case $PWD/ in "$T"/*) ;; *) echo "git shim: refusing to run outside the test dir ($PWD)" >&2; exit 97 ;; esac
if [ "${1:-}" = push ]; then
  url=$("$REAL_GIT" remote get-url origin 2>/dev/null)
  case $url in "$T"/*) ;; *) echo "git shim: refusing push to $url" >&2; exit 96 ;; esac
fi
exec "$REAL_GIT" "$@"'
mk secretscan 'echo "secretscan $*" >> "$FAKE/calls.log"; exec "$REAL_SECRETSCAN" "$@"'
export PATH=$T/shims:$PATH

pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2" | head -30; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if [[ "$2" == *"$3"* ]]; then ok "$1"; else no "$1" "$2"; fi; }
hasnt() { if [[ "$2" == *"$3"* ]]; then no "$1" "$2"; else ok "$1"; fi; }
R=$T/repo
g() { "$REAL_GIT" -C "$R" "$@"; }
o() { "$REAL_GIT" -C "$T/origin.git" "$@"; }
run() { (cd "$T" && "$V" "$@") > "$T/out" 2> "$T/err"; rc=$?; out=$(cat "$T/out"); err=$(cat "$T/err"); }
pushes() { grep -c '^git push' "$FAKE/git.log" 2>/dev/null; }
notes() { ls -A "$HOME/inbox" | wc -l; }
clean() { rm -f "$FAKE"/*.log "$HOME/inbox"/*; }

# ---- fixtures: sources, a bare origin and a clone with upstream + identity
mkdir -p "$T/src/bin" "$T/src/opt/tgread-tests/__pycache__" "$T/src/opt/tgread-tests/bench" "$T/src/opt/llm-local-tests/sub"
printf '#!/bin/sh\necho llm v1\n' > "$T/src/bin/llm-local"; chmod 755 "$T/src/bin/llm-local"
printf 'PATTERNS = []\n' > "$T/src/bin/secretscan.py"; chmod 644 "$T/src/bin/secretscan.py"
printf '#!/usr/bin/env python3\nprint("bills v1")\n' > "$T/src/bin/bills"; chmod 750 "$T/src/bin/bills"
touch -d '2026-10-01 12:00' "$T/src/bin/llm-local"
echo 'def test_a(): pass' > "$T/src/opt/tgread-tests/test_a.py"
echo junk > "$T/src/opt/tgread-tests/__pycache__/test_a.cpython-311.pyc"
echo junk > "$T/src/opt/tgread-tests/stray.pyc"
echo big > "$T/src/opt/tgread-tests/bench/huge.json"
echo 'echo ok' > "$T/src/opt/llm-local-tests/sub/run.sh"
"$REAL_GIT" init -q --bare -b main "$T/origin.git"
"$REAL_GIT" clone -q "$T/origin.git" "$R" 2>/dev/null
g config user.name "Test Bot"; g config user.email "test@example.invalid"
mkdir -p "$R/vendor" "$R/tests/tgread" "$R/tests/llm-local" "$R/app"
echo 'old' > "$R/vendor/bills"; echo 'stale test' > "$R/tests/tgread/test_old.py"; echo 'app' > "$R/app/main.py"
echo '__pycache__/' > "$R/.gitignore"
g add -A; g commit -q -m init; g push -q -u origin main 2>/dev/null
init=$(g rev-parse HEAD)

echo "# guard: the git shim never touches anything outside the test dir"
(cd / && git status >/dev/null 2>&1); eq "git shim refuses outside \$T" "$?" "97"

echo "# first sync: vendor + mirror + commit + push"
clean; run
eq "rc 0" "$rc" "0"
eq "stdout: pushed (short sha)" "$out" "vault-git-sync: pushed ($(g rev-parse --short HEAD))"
eq "stderr empty" "$err" ""
for f in llm-local secretscan.py bills; do
  cmp -s "$T/src/bin/$f" "$R/vendor/$f"; eq "vendor/$f copied" "$?" "0"
done
eq "cp -p keeps the mode" "$(stat -c %a "$R/vendor/bills")" "750"
eq "cp -p keeps the mtime" "$(stat -c %Y "$R/vendor/llm-local")" "$(stat -c %Y "$T/src/bin/llm-local")"
eq "tests mirrored (tgread)" "$(cat "$R/tests/tgread/test_a.py" 2>/dev/null)" "def test_a(): pass"
eq "tests mirrored (llm-local, nested)" "$(cat "$R/tests/llm-local/sub/run.sh" 2>/dev/null)" "echo ok"
eq "--delete drops files gone from ~/opt" "$([ -e "$R/tests/tgread/test_old.py" ] && echo there || echo gone)" "gone"
eq "__pycache__ excluded" "$([ -e "$R/tests/tgread/__pycache__" ] && echo there || echo absent)" "absent"
eq "*.pyc excluded" "$([ -e "$R/tests/tgread/stray.pyc" ] && echo there || echo absent)" "absent"
eq "bench excluded" "$([ -e "$R/tests/tgread/bench" ] && echo there || echo absent)" "absent"
eq "untouched repo files stay" "$(cat "$R/app/main.py")" "app"
msg=$(g log -1 --format=%s)
[[ $msg =~ ^auto:\ sync\ [0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}$ ]] && ok "default message 'auto: sync DATE HH:MM'" || no "default message" "$msg"
eq "one new commit" "$(g rev-list --count "$init"..HEAD)" "1"
eq "origin main == HEAD" "$(o rev-parse main)" "$(g rev-parse HEAD)"
eq "work tree clean" "$(g status --porcelain)" ""
eq "secretscan got the diff via --stdin" "$(cat "$FAKE/calls.log")" "secretscan --stdin"
eq "no inbox note on success" "$(notes)" "0"

echo "# nothing to do: silent, no commit, no push"
clean; head0=$(g rev-parse HEAD); run
eq "rc 0, no output" "$rc|$out|$err" "0||"
eq "no new commit" "$(g rev-parse HEAD)" "$head0"
eq "no push attempted" "$(pushes)" "0"
eq "secretscan not run without a diff" "$(cat "$FAKE/calls.log" 2>/dev/null)" ""

echo "# custom message (vault admin deploy/release)"
clean; printf '#!/bin/sh\necho llm v2\n' > "$T/src/bin/llm-local"; run "deploy 2026-10-15 04:00"
eq "rc 0" "$rc" "0"
eq "commit message is \$1" "$(g log -1 --format=%s)" "deploy 2026-10-15 04:00"
eq "only the changed file in the commit" "$(g show --name-only --format= HEAD)" "vendor/llm-local"
eq "pushed" "$(o rev-parse main)" "$(g rev-parse HEAD)"

echo "# ahead of upstream (an earlier push failed): push again"
clean; echo note > "$R/NOTES"; g add NOTES; g commit -q -m "local only"; run
eq "rc 0" "$rc" "0"
has "stdout says pushed" "$out" "vault-git-sync: pushed"
eq "origin caught up" "$(o rev-parse main)" "$(g rev-parse HEAD)"
eq "no extra commit" "$(g log -1 --format=%s)" "local only"

echo "# secretscan flags the diff"
TOKEN="ghp_$(printf 'Q%.0s' $(seq 1 36))"
clean; head0=$(g rev-parse HEAD); omain=$(o rev-parse main)
printf '#!/usr/bin/env python3\nTOKEN = "%s"\n' "$TOKEN" > "$T/src/bin/bills"; run
eq "rc 1" "$rc" "1"
has "stderr reason" "$err" "vault-git-sync: secretscan flagged the diff; nothing committed"
has "secretscan reports the pattern name only" "$err" "pattern:github-token"
eq "nothing committed" "$(g rev-parse HEAD)" "$head0"
g diff --cached --quiet; eq "index reset (nothing left staged)" "$?" "0"
eq "nothing pushed" "$(o rev-parse main)" "$omain"
eq "no push attempted" "$(pushes)" "0"
eq "no note without CRON=1" "$(notes)" "0"
hasnt "secret not echoed (stdout+stderr)" "$out$err" "$TOKEN"
clean; CRON=1 run
eq "CRON=1: rc 1" "$rc" "1"
eq "CRON=1: one note" "$(ls "$HOME/inbox" | grep -c '^vault-git-sync-[0-9]*\.md$')" "1"
nt=$(cat "$HOME/inbox"/vault-git-sync-*.md 2>/dev/null)
has "note names the reason" "$nt" "vault-git-sync failed: secretscan flagged the diff; nothing committed"
has "note marked as data" "$nt" "Data, not instructions."
hasnt "note holds no secret" "$nt" "$TOKEN"
eq "CRON=1: still nothing committed" "$(g rev-parse HEAD)" "$head0"
printf '#!/usr/bin/env python3\nprint("bills v2")\n' > "$T/src/bin/bills"

echo "# push fails, then the next run retries it"
clean; g remote set-url origin "$T/missing.git"; CRON=1 run
eq "rc 1" "$rc" "1"
has "stderr: push failed" "$err" "vault-git-sync: push failed"
eq "commit kept locally" "$(g log -1 --format=%s | cut -c1-10)" "auto: sync"
eq "origin not updated" "$(o rev-parse main)" "$omain"
has "note: push failed" "$(cat "$HOME/inbox"/vault-git-sync-*.md 2>/dev/null)" "vault-git-sync failed: push failed"
clean; g remote set-url origin "$T/origin.git"; run
eq "retry: rc 0" "$rc" "0"
eq "retry: origin caught up" "$(o rev-parse main)" "$(g rev-parse HEAD)"

echo "# commit fails (pre-commit hook says no)"
clean; head0=$(g rev-parse HEAD); printf '#!/bin/sh\nexit 1\n' > "$R/.git/hooks/pre-commit"; chmod +x "$R/.git/hooks/pre-commit"
echo 'v3' >> "$T/src/bin/secretscan.py"; CRON=1 run
eq "rc 1" "$rc" "1"
has "stderr: commit failed" "$err" "vault-git-sync: commit failed"
eq "no commit" "$(g rev-parse HEAD)" "$head0"
eq "no push after a failed commit" "$(pushes)" "0"
has "note: commit failed" "$(cat "$HOME/inbox"/vault-git-sync-*.md 2>/dev/null)" "commit failed"
rm "$R/.git/hooks/pre-commit"; clean; run; eq "after the hook is gone: rc 0" "$rc" "0"

echo "# missing vendor source: old copy kept, commits locally, ONE note per file, push skipped"
clean; mv "$T/src/bin/bills" "$T/bills.away"; before=$(cat "$R/vendor/bills"); omain=$(o rev-parse main)
echo 'def test_b(): pass' > "$T/src/opt/tgread-tests/test_b.py"; run
eq "rc 1" "$rc" "1"
has "stderr names the missing source" "$err" "vault-git-sync: source missing or unreadable: $T/src/bin/bills"
has "stderr: push skipped" "$err" "vault-git-sync: push skipped, 1 source(s) missing: $T/src/bin/bills"
eq "old vendored copy kept" "$(cat "$R/vendor/bills")" "$before"
eq "new test still committed locally" "$(g show --name-only --format= HEAD)" "tests/tgread/test_b.py"
eq "no push attempted" "$(pushes)" "0"
eq "origin unchanged" "$(o rev-parse main)" "$omain"
eq "one note, fixed name (no CRON needed: cron discards stderr anyway)" "$(ls "$HOME/inbox")" "vault-git-sync-missing-bills.md"
nt=$(cat "$HOME/inbox/vault-git-sync-missing-bills.md")
has "note names the source" "$nt" "source missing or unreadable: $T/src/bin/bills"
has "note says the push is skipped" "$nt" "pushes to github example-user/vault-private are skipped"
has "note marked as data" "$nt" "Data, not instructions."
echo "# still missing next run (CRON=1), nothing else new: rc 1, no push, note deduped (not rewritten)"
rm -f "$FAKE"/*.log; touch -d '2026-10-01 00:00' "$HOME/inbox/vault-git-sync-missing-bills.md"
m0=$(stat -c %Y "$HOME/inbox/vault-git-sync-missing-bills.md"); head0=$(g rev-parse HEAD); CRON=1 run
eq "rc 1 (not the silent nothing-to-do exit)" "$rc" "1"
eq "no push attempted" "$(pushes)" "0"
eq "no new commit" "$(g rev-parse HEAD)" "$head0"
eq "still one note" "$(ls "$HOME/inbox")" "vault-git-sync-missing-bills.md"
eq "note not rewritten" "$(stat -c %Y "$HOME/inbox/vault-git-sync-missing-bills.md")" "$m0"
eq "no generic failure note either" "$(ls "$HOME/inbox" | grep -c '^vault-git-sync-[0-9]')" "0"
echo "# a second source goes missing (a test dir): its own note; --delete never wipes the mirrored tests"
mv "$T/src/opt/llm-local-tests" "$T/llm-local-tests.away"; rm -f "$FAKE"/*.log; run
eq "rc 1" "$rc" "1"
eq "stderr: exactly one line per missing source + the skip line (no cp/rsync noise)" "$err" "vault-git-sync: source missing or unreadable: $T/src/bin/bills (old copy kept, push skipped)
vault-git-sync: source missing or unreadable: $T/src/opt/llm-local-tests/ (old copy kept, push skipped)
vault-git-sync: push skipped, 2 source(s) missing: $T/src/bin/bills $T/src/opt/llm-local-tests/"
eq "two notes, one per source" "$(ls "$HOME/inbox" | tr '\n' ' ')" "vault-git-sync-missing-bills.md vault-git-sync-missing-llm-local-tests.md "
has "test-dir note names it" "$(cat "$HOME/inbox/vault-git-sync-missing-llm-local-tests.md")" "$T/src/opt/llm-local-tests/"
eq "mirrored tests kept" "$(cat "$R/tests/llm-local/sub/run.sh" 2>/dev/null)" "echo ok"
has "stderr counts both" "$err" "push skipped, 2 source(s) missing"
eq "no push attempted" "$(pushes)" "0"
echo "# both back: the next run pushes the held-back commit, no new note"
mv "$T/bills.away" "$T/src/bin/bills"; mv "$T/llm-local-tests.away" "$T/src/opt/llm-local-tests"
clean; run
eq "rc 0" "$rc" "0"
has "stdout: pushed" "$out" "vault-git-sync: pushed"
eq "origin caught up (incl. the held-back commit)" "$(o rev-parse main)" "$(g rev-parse HEAD)"
eq "origin has test_b" "$(o show main:tests/tgread/test_b.py 2>/dev/null)" "def test_b(): pass"
eq "no note" "$(notes)" "0"
echo "# goes missing while the repo is fully in sync: still rc 1 + note (not the silent nothing-to-do exit)"
clean; mv "$T/src/bin/llm-local" "$T/llm.away"; run; mv "$T/llm.away" "$T/src/bin/llm-local"
eq "rc 1" "$rc" "1"
eq "note for llm-local" "$(ls "$HOME/inbox")" "vault-git-sync-missing-llm-local.md"
eq "no push attempted" "$(pushes)" "0"
echo "# present but unreadable source: same as missing"
clean; chmod 000 "$T/src/bin/secretscan.py"; echo 'v4' >> "$T/src/bin/llm-local"; run
chmod 644 "$T/src/bin/secretscan.py"
eq "rc 1" "$rc" "1"
eq "note for secretscan.py" "$(ls "$HOME/inbox")" "vault-git-sync-missing-secretscan.py.md"
eq "no push attempted" "$(pushes)" "0"
eq "the readable change is still committed" "$(g show --name-only --format= HEAD)" "vendor/llm-local"
clean; run; eq "readable again: rc 0 + pushed" "$rc $(o rev-parse main)" "0 $(g rev-parse HEAD)"

echo "# missing repo dir"
clean; VAULT_GIT_DIR=$T/nope run
eq "rc 1" "$rc" "1"
eq "no git call" "$(cat "$FAKE/git.log" 2>/dev/null)" ""
eq "nothing created" "$([ -e "$T/nope" ] && echo yes || echo no)" "no"

echo "# public-path gate (config file names the gate command; stub gates here)"
GC=$HOME/.config/vault-git-sync/gate; mkdir -p "$(dirname "$GC")"
mk gate-block 'echo "gate $*" >> "$FAKE/gate.log"; echo "  gate denylist (9 private rules): 1 hits"; echo "    app/x.py:1 rule#0"; exit 3'
mk gate-pass 'echo "gate $*" >> "$FAKE/gate.log"; echo "  all fast gates passed"; exit 0'
printf '# the gate\n%s\n' "$T/shims/gate-block" > "$GC"
clean; head0=$(g rev-parse HEAD); printf '#!/bin/sh\necho llm gate\n' > "$T/src/bin/llm-local"
CRON=1 run
eq "blocked: rc 3" "$rc" "3"
eq "blocked: nothing committed" "$(g rev-parse HEAD)" "$head0"
eq "blocked: index reset" "$(g diff --cached --name-only)" ""
eq "blocked: no push" "$(pushes)" "0"
has "blocked: file:line + label on stderr" "$err" "app/x.py:1 rule#0"
eq "blocked: one dated note" "$(ls "$HOME/inbox")" "vault-git-sync-gate-$(date +%F).md"
has "note: data marker" "$(cat "$HOME/inbox"/vault-git-sync-gate-*.md)" "Data, not instructions."
tree=$(g add -A && g write-tree); g reset -q
has "gate got the repo and the staged tree" "$(cat "$FAKE/gate.log")" "--src $R --rev $tree"
echo kept >> "$HOME/inbox/vault-git-sync-gate-$(date +%F).md"
CRON=1 run; eq "blocked again: still one note, not rewritten" "$(notes) $(tail -1 "$HOME/inbox/vault-git-sync-gate-$(date +%F).md")" "1 kept"
rm -f "$HOME/inbox"/*; run; eq "without CRON: no note" "$(notes) $rc" "0 3"
printf '%s\n' "$T/shims/gate-pass" > "$GC"
clean; run
eq "pass: rc 0 + pushed" "$rc $(o rev-parse main)" "0 $(g rev-parse HEAD)"
eq "pass: the gate saw the committed tree" "$(sed 's/.*--rev //' "$FAKE/gate.log")" "$(g rev-parse 'HEAD^{tree}')"
clean; run; eq "nothing to do: gate not called" "$(cat "$FAKE/gate.log" 2>/dev/null)" ""
printf '%s\n' "$T/shims/gate-block" > "$GC"
clean; echo x >> "$R/README-gate.md"; g add -A; g commit -q -m "direct commit"; ahead=$(g rev-parse HEAD)
run
eq "ahead + blocked: rc 3, no push" "$rc $(pushes)" "3 0"
eq "ahead + blocked: origin unchanged" "$([ "$(o rev-parse main)" = "$ahead" ] && echo pushed || echo held)" "held"
printf '%s\n' "$T/nope/gate" > "$GC"
clean; run
eq "gate cannot run: rc 1, no push" "$rc $(pushes)" "1 0"
has "gate cannot run: says so" "$err" "could not run"
printf '# only a comment\n' > "$GC"
clean; run
eq "empty gate config: rc 1, no push" "$rc $(pushes)" "1 0"
rm -f "$GC"
clean; run
eq "no gate config: pushed as before" "$rc $(o rev-parse main)" "0 $(g rev-parse HEAD)"

echo "# defaults (static: never run against the real repo)"
src=$(cat "$V")
has "default repo" "$src" '"${VAULT_GIT_DIR:-/home/node/opt/claude-vault}"'
has "default vendor source" "$src" 'SRC_BIN=${VAULT_GIT_SRC_BIN:-/home/node/bin}'
has "default tests source" "$src" 'SRC_OPT=${VAULT_GIT_SRC_OPT:-/home/node/opt}'
has "pushes origin HEAD with a timeout" "$src" 'timeout 120 git push -q origin HEAD'

echo "# isolation"
eq "real vault repo untouched (HEAD + status)" "$(real_state)" "$real_before"
eq "no note in the real inbox" "$(real_notes)" "$real_notes_before"
eq "every push went to the local origin" "$(grep -c 'refusing push' "$T/err" 2>/dev/null)" "0"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
