#!/bin/sh
# PreToolUse hook on Bash (settings.json, next to vault-guard): the risk check (Hermes idea 2).
# Rules, LLM fallback and the risk-ok CLI: risk_check.py next to this file; tests: test_risk_check.py.
# FAILS OPEN (unlike vault-guard): a safety net, not a gate. This wrapper always exits 0. A crash, a missing script
# or a hang past 12 s allows the call and leaves an ERROR line in ~/.local/state/risk-check.log.
# Fast path: a payload without any trigger word (most commands) is allowed by one grep, without starting python.
# The grep must stay a superset of risk_check.py's PREFILTER, on JSON-escaped text (hence the \\[bfnrt] boundary).
in=$(cat) || exit 0
printf '%s' "$in" | grep -qE '(^|[^[:alnum:]_.-]|\\[bfnrt])(ssh|scp|rsync|sftp|rm|unlink|find|shred|truncate|crontab|curl|wget|docker|docker-compose|tg|gh|git|vault|proxy|risk-ok|mail|mailx|sendmail|msmtp|mutt|swaks|mkfs|mke2fs|mkswap|wipefs|parted|fdisk|cfdisk|sgdisk|gdisk|sfdisk|blkdiscard|dd|zfs|zpool|btrfs|xfs_repair|cryptsetup|hdparm)([^[:alnum:]_-]|$)|\.secrets|\.env|/dev/(sd|nvme|md|hd|vd|xvd|mapper|disk|loop|dm-|mmcblk|nbd)' || exit 0
out=$(printf '%s' "$in" | timeout -k 1 "${RISK_CHECK_TIMEOUT:-12}" /usr/bin/python3 -I "${0%/*}/risk_check.py" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ]; then
  [ -n "$out" ] && printf '%s\n' "$out"
else
  printf '%s ERROR wrapper: risk_check.py exit %s; call allowed (fail open)\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$rc" \
    >>"${RISK_CHECK_LOG:-/home/node/.local/state/risk-check.log}" 2>/dev/null
fi
exit 0
