#!/bin/sh
# PreToolUse hook (settings.json, matcher Bash|Read|Grep|Glob|Edit|Write|MultiEdit|NotebookEdit): the privacy vault's
# level-1 tripwire. Logic and rules: vault_guard.py next to this file; tests: test_vault_guard.py.
# Fails closed (2026-10-12): exit 0 = decided (deny JSON on stdout, or nothing = allow), exit 2 = blocked (reason on
# stderr). Anything else (python crash, missing file, a hang past 8 s) is turned into exit 2 here; settings.json's
# "onFailure": "block" also blocks when this wrapper can't start or hits the 10 s hook timeout.
dir=$(dirname "$0")
rc=missing
if [ -r "$dir/vault_guard.py" ]; then        # (python itself exits 2 on a missing script: say why instead)
  timeout -k 1 8 /usr/bin/python3 -I "$dir/vault_guard.py"
  rc=$?
  case $rc in 0|2) exit $rc ;; esac
fi
echo "vault-guard: the guard failed ($rc); tool call blocked (fail closed). If every call is blocked, the guard" \
  "itself is broken: tell the owner; fix from a container shell: brain log -- .claude/hooks, then" \
  "brain restore .claude/hooks REV." >&2
exit 2
