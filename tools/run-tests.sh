#!/usr/bin/env bash
# Run the offline test suites against THIS checkout: each suite's tool is bin/<suite> (via <SUITE>_BIN).
# usage: tools/run-tests.sh [--ci] [suite ...]   --ci skips the suites listed in tools/ci-skip.txt
#        tools/run-tests.sh --plugins            the plugin tests: telegram-plus (bun) and heartbeat (node)
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG=${TEST_LOG_DIR:-$(mktemp -d)}
# Tests never read a real profile: every suite gets the synthetic fixture (values differ from the defaults).
export ASSISTANT_PROFILE="$ROOT/tests/profile/fixtures/profile.json"
ci=0; [ "${1:-}" = --ci ] && { ci=1; shift; }
if [ "${1:-}" = --plugins ]; then
  pass=0; fail=0; failed=()
  part() {   # part NAME DIR CMD...: one plugin test command; its log is shown when it fails
    local n=$1 d=$2; shift 2
    if (cd "$d" && timeout "${TEST_TIMEOUT:-600}" "$@") >"$LOG/plugin-$n.log" 2>&1 </dev/null; then
      pass=$((pass+1)); echo "ok   plugins/$n"
    else fail=$((fail+1)); failed+=("plugins/$n"); echo "FAIL plugins/$n (log: $LOG/plugin-$n.log)"
      tail -n 60 "$LOG/plugin-$n.log"; fi
  }
  part telegram-plus "$ROOT/plugins/telegram-plus" bash -c 'bun install --frozen-lockfile && bun test'
  # The test files by shell glob, not the directory: Node 22 resolves `node --test DIR` as a module path and
  # fails ("Cannot find module"). The glob also leaves out live-session.sh, which needs a running session.
  part heartbeat "$ROOT" bash -c 'node --test plugins/heartbeat/test/*.test.mjs'
  echo "passed $pass, failed $fail${failed[*]:+: ${failed[*]}} (logs: $LOG)"
  [ $fail = 0 ]; exit
fi
if [ $# -gt 0 ]; then suites=("$@"); else mapfile -t suites < <(cd "$ROOT/tests" && ls -d */ | tr -d /); fi
skip=" "
[ $ci = 1 ] && [ -f "$ROOT/tools/ci-skip.txt" ] && skip=" $(grep -v '^#' "$ROOT/tools/ci-skip.txt" | cut -d' ' -f1 | tr '\n' ' ') "
pass=0; fail=0; failed=()
for s in "${suites[@]}"; do
  case "$skip" in *" $s "*) echo "skip $s"; continue;; esac
  d="$ROOT/tests/$s"; var=$(echo "$s" | tr 'a-z-' 'A-Z_')_BIN
  [ -e "$ROOT/bin/$s" ] && export "$var=$ROOT/bin/$s"
  # A suite runs its run.sh / run.py (if any), then every test_*.py that the runner script does not already
  # name (or cover with a test_* glob). All parts run; the suite fails if any part fails.
  cmds=(); runner=""
  if [ -f "$d/run.sh" ]; then runner="$d/run.sh"; cmds+=("bash	$d/run.sh")
  elif [ -f "$d/run.py" ]; then runner="$d/run.py"; cmds+=("py	$d/run.py"); fi
  for t in "$d"/test_*.py; do
    [ -f "$t" ] || continue
    [ -n "$runner" ] && grep -qE "$(basename "$t" | sed 's/\./\\./g')|test_\*" "$runner" && continue
    cmds+=("py	$t")
  done
  [ ${#cmds[@]} -gt 0 ] || { echo "---- $s: no runner"; unset "$var"; continue; }
  : >"$LOG/$s.log"; ok=1
  for c in "${cmds[@]}"; do
    f=${c#*	}; case $c in bash*) cmd=(bash "$f");; *) cmd=(python3 -I "$f");; esac
    echo "== $(basename "$f")" >>"$LOG/$s.log"
    (cd "$ROOT" && timeout "${TEST_TIMEOUT:-600}" "${cmd[@]}") >>"$LOG/$s.log" 2>&1 </dev/null || ok=0
  done
  if [ $ok = 1 ]; then pass=$((pass+1)); echo "ok   $s (${#cmds[@]} part(s))"
  else fail=$((fail+1)); failed+=("$s"); echo "FAIL $s (log: $LOG/$s.log)"; fi
  unset "$var"
done
if [ $# -eq 0 ]; then for t in "$ROOT"/claude/hooks/test_*.py; do
  n=hooks/$(basename "$t" .py)
  if (cd "$ROOT" && timeout 600 python3 -I "$t") >"$LOG/$(basename "$t").log" 2>&1 </dev/null; then pass=$((pass+1)); echo "ok   $n"
  else fail=$((fail+1)); failed+=("$n"); echo "FAIL $n"; fi
done; fi
echo "passed $pass, failed $fail${failed[*]:+: ${failed[*]}} (logs: $LOG)"
[ $fail = 0 ]
