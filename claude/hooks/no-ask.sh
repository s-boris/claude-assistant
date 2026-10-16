#!/bin/sh
# Hook for AskUserQuestion (settings.json: PreToolUse and PermissionRequest, matcher AskUserQuestion; 2026-10-13).
# Nobody watches this terminal, so the tool is also in permissions.deny. This is the second safeguard for
# anthropics/claude-code#101793: after a refusal the CLI itself can raise a terminal-only AskUserQuestion
# permission dialog, which blocks the Telegram-driven session until someone restarts it.
#   no-ask.sh PreToolUse         deny the call; the model is told to ask on Telegram and carry on
#   no-ask.sh PermissionRequest  deny the dialog and end the turn (interrupt, as the issue's tested workaround);
#                                channel-check re-sends an owner message that the turn left unanswered
# Always exit 0 with a deny decision on stdout. It only ever runs for AskUserQuestion; if it fails, Claude Code
# treats that as a non-blocking hook error, i.e. today's behavior.
in=$(cat 2>/dev/null)
# defensive: if a matcher is ever widened, never deny other tools
case "$in" in *'"AskUserQuestion"'*) ;; *) exit 0 ;; esac
msg='AskUserQuestion is disabled here: nobody sees this terminal. Ask the owner on Telegram instead (reply tool, with buttons for choices), then carry on with other work until he answers.'
case "$1" in
  PermissionRequest)
    printf '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"%s","interrupt":true}}}\n' "$msg" ;;
  *)
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$msg" ;;
esac
exit 0
