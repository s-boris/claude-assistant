# Vault troubleshooting
Read when a `vault`, `tgread` or `paperless` command fails or behaves oddly. The rules live in ~/.claude/skills/vault/SKILL.md and win over this file on any conflict.

## Exit codes
- Exit codes: 0 ok, 2 usage (also "too much for the LLM"), 3 not logged in or vault unreachable (stderr says which;
  `vault admin ps`), 4 GUARD BLOCKED (tell the owner, don't retry), 6 local LLM unavailable, 10 waiting for the
  owner (his ✅ tap or code), 75 try later (FLOOD_WAIT / sync or LLM busy), 77 vault token rejected/missing.
- `vault job wait`: exit 0 = done, 12 = still running (wait again; never one call over 4 min).
- `vault raw release` / `--text` / `--content` / tier-2 `paperless fields`: 10 = waiting for his ✅ (run it again later,
  don't nag him); 1 = denied or expired (ask again only if he wants).
- `vault ask --session ID …`: exit 1 "session_gone" (15 min idle, 60 min cap, 6 turns, vault restart, tg purge): just
  ask again. 409 sessions_off: sessions are switched off (admin-deploy.md, Switches).

## Guard blocks
~/.claude/hooks/vault-guard.sh (PreToolUse, settings.json) denies tool calls that reach around the vault: tgread's
local data/secrets, `--raw`, `docker exec/cp/export claude-vault`, the volume claude-vault_data, ssh/scp/rsync to
/mnt/user/appdata/claude-vault or /proc/*/root, the bot token file (also recursive greps over ~ or ~/.claude that
would include it) and Bot API forward/copy/edit/getUpdates calls, (VAULT_MODE on) direct paperless
(the server's port 8000, ~/.secrets/paperless.json), and in subagents only (the hook payload carries agent_id)
`vault|tgread|paperless … --text|--content` (rule vault-text-subagent). Bash rules match the command TEXT, so even an `echo` or heredoc
containing such a string is denied: write such text with the Write/Edit tools (they're checked by path only).
- A block is the guard working: use the vault command instead, never work around it (also not by switching tools);
  report it. Exit 4 from tgread = GUARD BLOCKED: tell the owner, don't retry.
- Every call blocked "(fail closed)": tell the owner; fix from a container shell: `brain restore .claude/hooks REV`
  (admin-deploy.md, Changing the guard).

## Symptoms
- LLM down (exit 6): there is no fallback; retry later, `vault status`.
- Slow interactive read: `gmail get`/`tg search` during a summary job take ~1 min (per-request gating, internals.md).
  A tg read whose caller gave up still finishes, so just retry (cached).
- Job id unknown or results gone: a vault restart drops all jobs (submit again: parts are cached); results are kept
  30 min.
- Pending raw requests gone: a vault restart drops pending requests; request again only if he still wants it.
- Input above 40000 characters: exit 2 with advice (tgread: narrow with `--chat`, `--hours`, `-n`, `--per-chat`,
  `--since`, or `--llm-max-chars N` in the background).
- Approval mode "code" in `vault status`:
Fallback "code" mode (`vault status` shows it): only while the vault's bot can't poll (normally: buttons, its own
bot polling; the code mode applies while that bot is shared or broken) the message carries a 6-digit code instead
→ he tells me → `vault raw release ID CODE` (3 wrong codes kill it). At the Unraid console he can always `vault-console approve RID`.
- Vault bot "shared" or a revoked bot token: a 409 Conflict (someone else polls that bot) marks it shared for good
  (alert; code fallback). The fix is a NEW bot: he runs `vault-console secret telegram` (setup.md, Owner console);
  that is also the only fix when the bot token changed (BotFather revoke), since `vault secret rotate` would need a
  code sent with the dead token. While `vault status` says "vault bot: shared", document and finance intake are off.
- Gmail account auth_failed:
- `vault gmail status`: per account `[label] …@domain`, watcher idle/reconnecting/auth_failed/poll-only, last poll,
  positions. A refused login blocks that account's retries for 30 min (Google locks accounts that keep failing) and
  alerts (vault-gmail-auth[-LABEL] note): he sets a new app password with the console command above (+ the label).
  `vault status` and the pinned board show the worst account; with several accounts the board adds one ✉️ line per
  label.
- Media files: `vault media status` shows what is still here; the vault alerts when the `vault media gc` report is
  overdue (cron */5).
