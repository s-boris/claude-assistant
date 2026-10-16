#!/bin/sh
# PreToolUse hook on Agent (settings.json, matcher "Agent"; 2026-10-15): appends the standard [subagent-rules] block to
# a subagent prompt that lacks it and sets effort xhigh when the call has none (not for forks, not when the agent's own
# definition sets an effort). Logic: subagent_rules.py next to this file; tests: test_subagent_rules.py.
# FAILS OPEN: this wrapper always exits 0. A crash, a missing script or a hang past 4 s prints nothing, so the Agent
# call runs unchanged, and leaves an ERROR line in ~/.local/state/subagent-rules.log.
in=$(cat) || exit 0
out=$(printf '%s' "$in" | timeout -k 1 "${SUBAGENT_RULES_TIMEOUT:-4}" /usr/bin/python3 -I "${0%/*}/subagent_rules.py" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ]; then
  [ -n "$out" ] && printf '%s\n' "$out"
else
  printf '%s ERROR wrapper: subagent_rules.py exit %s; Agent call left unchanged (fail open)\n' \
    "$(date +%Y-%m-%dT%H:%M:%S)" "$rc" >>"${SUBAGENT_RULES_LOG:-/home/node/.local/state/subagent-rules.log}" 2>/dev/null
fi
exit 0
