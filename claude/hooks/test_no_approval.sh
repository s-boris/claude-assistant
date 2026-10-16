#!/bin/sh
# Unit tests for no-approval.sh (PermissionRequest deny hook for Bash). Run: sh ~/.claude/hooks/test_no_approval.sh
H=${1:-/home/node/.claude/hooks/no-approval.sh}
L=$(mktemp); export NO_APPROVAL_LOG=$L
pass=0; fail=0
ok() { pass=$((pass+1)); }
bad() { fail=$((fail+1)); echo "FAIL: $1"; }

# 1. Bash -> deny JSON with message, interrupt false
out=$(printf '%s' '{"hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"D=/x; rm -rf $D/*"},"agent_id":"a123"}' | sh "$H")
[ "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.decision.behavior')" = deny ] && ok || bad "bash not denied: $out"
[ "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.hookEventName')" = PermissionRequest ] && ok || bad "eventName"
[ "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.decision.interrupt')" = false ] && ok || bad "interrupt not false"
printf '%s' "$out" | jq -r '.hookSpecificOutput.decision.message' | grep -q 'find /abs/dir -mindepth 1 -delete' && ok || bad "hint missing"
printf '%s' "$out" | jq -r '.hookSpecificOutput.decision.message' | grep -qF '"${D:?}"/"$x"' && ok || bad "D:? hint missing"
# 2. log line with agent id and command
grep -q 'deny agent=a123 cmd=D=/x; rm -rf \$D/\*' "$L" && ok || bad "log line: $(cat "$L")"
# 3. main session (no agent_id) -> agent=main
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"cd x; rm -rf out/*"}}' | sh "$H" >/dev/null
grep -q 'agent=main cmd=cd x; rm -rf out/\*' "$L" && ok || bad "main log"
# 4. other tools -> no output, exit 0
for t in AskUserQuestion Edit Write Read mcp__x__y; do
  out=$(printf '{"tool_name":"%s","tool_input":{"command":"rm -rf /"}}' "$t" | sh "$H"); rc=$?
  [ -z "$out" ] && [ $rc -eq 0 ] && ok || bad "$t produced output: $out"
done
# 5. garbage / empty input -> no output, exit 0
out=$(printf 'not json' | sh "$H"); rc=$?; [ -z "$out" ] && [ $rc -eq 0 ] && ok || bad "garbage: $out"
out=$(sh "$H" </dev/null); rc=$?; [ -z "$out" ] && [ $rc -eq 0 ] && ok || bad "empty: $out"
# 6. secrets redacted in the log, command cut to 80 chars, newlines flattened
# fake token assembled at runtime so no token-shaped literal sits in this file (secretscan blocks brain commits)
fake=$(printf 'gh%s_%s%s' p abcdefghijklmnopqrstuvwxyz 0123456789AB)
jq -nc --arg c "curl -H token=$fake x
rm -rf \$T/*" '{tool_name:"Bash",tool_input:{command:$c}}' | sh "$H" >/dev/null
grep -qF "$(printf '%s' "$fake" | cut -c1-14)" "$L" && bad "secret in log" || ok
[ "$(tail -1 "$L" | sed 's/.* cmd=//' | wc -c)" -le 90 ] && ok || bad "not cut"
# token straddling char 80: redaction must happen before the cut (a cut token is not recognised)
jq -nc --arg c "echo 0123456789012345678901234567890123456789 x $fake" \
  '{tool_name:"Bash",tool_input:{command:$c}}' | sh "$H" >/dev/null
grep -qF "$(printf '%s' "$fake" | cut -c1-6)" "$L" && bad "partial secret in log: $(tail -1 "$L")" || ok
[ "$(wc -l <"$L")" -eq 4 ] && ok || bad "log has $(wc -l <"$L") lines, want 4"
# 7. unwritable log -> still denies
out=$(NO_APPROVAL_LOG=/nonexistent/dir/x.log sh "$H" <<'E'
{"tool_name":"Bash","tool_input":{"command":"rm -f *"}}
E
)
[ "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.decision.behavior')" = deny ] && ok || bad "unwritable log: $out"
# 8. quotes/backslashes in command survive (valid JSON)
out=$(printf '%s' '{"tool_name":"Bash","tool_input":{"command":"echo \"a\\\\b\" '"'"'q'"'"'; rm -rf $X"}}' | sh "$H")
printf '%s' "$out" | jq -e . >/dev/null 2>&1 && ok || bad "invalid json with quotes"
rm -f -- "$L"
echo "no-approval tests: $pass passed, $fail failed"
[ $fail -eq 0 ]
