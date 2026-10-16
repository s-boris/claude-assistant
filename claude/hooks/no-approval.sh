#!/bin/sh
# PermissionRequest hook for Bash (settings.json: PermissionRequest, matcher Bash; 2026-10-16).
# Nobody watches this terminal. bypassPermissions does not skip Claude Code's own safety checks (e.g. "Dangerous rm
# operation" on an rm target it can't resolve), so such a call waits for an approval nobody gives and hangs forever,
# in background subagents too (~/reports/tests/subagent-bash-hangs-2026-10-16.md). This hook answers every such
# approval prompt for Bash with an immediate deny plus a rewrite hint (interrupt false: the agent can retry).
# Normal Bash calls never reach PermissionRequest in bypassPermissions mode, so this only replaces hangs.
# Each deny is logged to ~/.local/state/no-approval.log (time, agent, first 80 chars of the command, redacted).
# Always exit 0. Non-Bash input: no output (the permission flow is unchanged). Test: test_no_approval.sh.
in=$(cat 2>/dev/null)
tool=$(printf '%s' "$in" | jq -r '.tool_name // empty' 2>/dev/null)
# defensive: if the matcher is ever widened, never touch other tools
[ "$tool" = "Bash" ] || exit 0
log=${NO_APPROVAL_LOG:-/home/node/.local/state/no-approval.log}
{
  agent=$(printf '%s' "$in" | jq -r '.agent_id // "main"' 2>/dev/null)
  cmd=$(printf '%s' "$in" | jq -r '.tool_input.command // ""' 2>/dev/null | tr '\n\t' '  ')
  # redact the whole command first, then cut: a token cut at char 80 is no longer recognised by secretscan
  ss=$(command -v secretscan 2>/dev/null || echo /home/node/bin/secretscan)
  if [ -x "$ss" ]; then
    cmd=$(printf '%s\n' "$cmd" | timeout 3 "$ss" --redact 2>/dev/null) || cmd='[redaction failed]'
  else
    cmd='[secretscan missing]'
  fi
  cmd=$(printf '%s' "$cmd" | cut -c1-80)
  printf '%s deny agent=%s cmd=%s\n' "$(date '+%F %T')" "${agent:-?}" "$cmd" >>"$log"
} 2>/dev/null
msg='Denied at once instead of waiting: Claude Code wanted an approval for this Bash command and nobody watches this terminal. Usually an rm whose target it cannot resolve (only variables, a variable glob like $D/*, a relative glob after cd, or a $(...) output). Rewrite and retry: use "${D:?}"/"$x", absolute literal paths, or find /abs/dir -mindepth 1 -delete; for a $(...) target, run that first and rm the literal paths in a separate call. Not an rm: simplify or split the command.'
jq -nc --arg m "$msg" '{hookSpecificOutput:{hookEventName:"PermissionRequest",decision:{behavior:"deny",message:$m,interrupt:false}}}' 2>/dev/null ||
  printf '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"Denied: Claude Code wanted an approval nobody can give. Rewrite rm targets as absolute literal paths or \\"${D:?}\\"/\\"$x\\", or use find /abs/dir -mindepth 1 -delete.","interrupt":false}}}\n'
exit 0
