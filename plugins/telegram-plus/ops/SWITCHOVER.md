# telegram-plus switchover / rollback

Status (2026-10-09): owner approved route A. Steps 1–5 are done; it goes live at the next restart.

Launcher (ops/start-claude, installed by ~/boot.sh): uses telegram-plus if it's in the plugin cache, else the official plugin.
Kill switch: `touch ~/.telegram-official && restart "rollback telegram"`. The official launcher is saved as ops/start-claude.official.

Why it needs a policy file: a custom channel plugin passed via plain `--channels` must be on Anthropic's server-side allowlist, otherwise it's skipped ("not on the approved channels allowlist").
- Route A: managed settings `channelsEnabled` + `allowedChannelPlugins` (ops/managed-settings.json). The list *replaces* the default allowlist, so the official plugin stays in it.
- Route B: `--dangerously-load-development-channels`. It shows a confirmation dialog on every start, so unattended restarts would hang. Don't use it, and never auto-confirm that dialog.

## Switch (route A)
1. `claude plugin marketplace add /home/node/plugins`
2. `claude plugin install telegram-plus@home-plugins && claude plugin disable --scope user telegram-plus@home-plugins`. Steps 1–2 edit ~/.claude/settings.json; back it up first. Enable it only via start-claude's `--settings`, so no other process loads it.
3. `sudo cp /usr/local/bin/start-claude /usr/local/bin/start-claude.official`
4. `sudo cp ops/managed-settings.json /etc/claude-code/managed-settings.json`, then `sudo cp ops/start-claude /usr/local/bin/start-claude`.
5. /etc and /usr/local/bin are rebuilt at container start, so add both `sudo cp` lines (absolute paths) to ~/boot.sh. The entrypoint runs `[ -x ~/boot.sh ] && ~/boot.sh` before tmux. Keep it idempotent.
6. `restart "switch to telegram-plus"`. Verify:
   - The server runs from `.../cache/home-plugins/telegram-plus/<ver>`.
   - There's exactly one telegram server.
   - No leftover daemon fork of the old session.
   - A Telegram round trip works: reply, a button press, a reaction.
7. Update CLAUDE.md:
   - tools are now `mcp__plugin_telegram-plus_telegram__*`
   - the source tag is `plugin:telegram-plus:telegram`
   - the access skill is `/telegram-plus:access`

## Changing the fork later
CAREFUL: the live server runs straight from this source dir. Its `CLAUDE_PLUGIN_ROOT` and cwd are /home/node/plugins/telegram-plus, because local directory marketplaces aren't copied. Any edit here goes live at the next server start, including a container restart. A syntax error would crash the plugin; the launcher falls back to the official plugin after 2 fast failures.

So never edit in place. Develop in a worktree:
```
git -C ~/plugins worktree add ~/jobs/tgp-dev
# edit there, then:
cd ~/jobs/tgp-dev/telegram-plus && bun test/offline.ts
```
Bump the version, commit, `git -C ~/plugins merge --ff-only <branch>`, then:
```
claude plugin marketplace update home-plugins
claude plugin update telegram-plus@home-plugins   # keeps the cache/version record in step
restart
```

## Watchdog (0.1.5)
`ops/channel-check` is installed as `~/bin/channel-check` (a copy: `install -m 755 ops/channel-check ~/bin/channel-check`),
run by `~/crontab` every minute. Tests: `python3 -I test/channel-check.py`. `channel-check --status` shows what it sees.
It works with older plugins too (falls back to Claude Code's MCP log and bot.pid).

## Hardening (2026-10-11)
start-claude's `--settings` also carries `"disableAgentView":true,"crossSessionInbound":"refuse"` and enables the heartbeat mod
(`heartbeat@home-plugins`, source ~/plugins/heartbeat, installed but disabled at user scope). start-claude.official has the same keys.
Never move `disableAgentView` into the env (CLAUDE_CODE_DISABLE_AGENT_VIEW) or user/managed settings: children and cron lose `claude agents --json`.
Mod off, no edits: `touch ~/.heartbeat-mod-off && restart "heartbeat mod off"`; back on: `rm ~/.heartbeat-mod-off && restart`. Cron keeps beating either way.
Full rollback: `git -C ~/plugins revert 8bf6874` (launcher), `sudo install -m 755 ~/plugins/telegram-plus/ops/start-claude /usr/local/bin/start-claude`,
`restart`; optionally `claude plugin uninstall heartbeat@home-plugins`. Changing the mod: edit, `node --test test/*.test.mjs` (offline only), bump
the version, commit, `claude plugin marketplace update home-plugins && claude plugin update heartbeat@home-plugins`, restart.
Idle backoff (mod 0.2.0, 2026-10-13): empty beats double the plain threshold up to 60 min (120 in 00-08 Zurich); notes still wake after 2-3 min.
The cron fallback follows it via ~/.local/bin/heartbeat (= `install -m 755 ~/plugins/heartbeat/ops/heartbeat-cron ~/.local/bin/heartbeat`; it
shadows /usr/local/bin/heartbeat on cron's PATH and reads idleMin/unanswered/nextDue/lastBeat from ~/.local/state/heartbeat-mod.json while
lastTick < 3 min old: no cron beat before nextDue + 3 min unless the mod's beats stop landing or an inbox note waits 5+ min; boot.sh keeps the copy current).
Rollback: `rm ~/.local/bin/heartbeat` (cron back to plain `heartbeat 10`), `git -C ~/plugins revert <commit>`, plugin update, restart.

## Rollback
1. `sudo cp /usr/local/bin/start-claude.official /usr/local/bin/start-claude`, then remove the lines from ~/boot.sh.
2. Optional: `sudo rm /etc/claude-code/managed-settings.json`. It still allows the official plugin, so keeping it is harmless.
3. `restart "rollback telegram"`
4. Optional: `claude plugin uninstall telegram-plus@home-plugins`, then `claude plugin marketplace remove home-plugins`.

The leftovers are harmless to the official plugin: the extra update types, commands.json, instant.json (0.1.8), and `forwardReactions` in access.json.

## Route 2 (owner declines the policy file)
Keep the official channel plugin for inbound. Run telegram-plus as a plain MCP server with `TELEGRAM_NO_POLL=1`, giving tools only and no getUpdates. That covers streaming drafts, reply keyboards, polls, pins and effects. Inbound reactions, button presses and poll answers won't arrive.

## If inbound/tools break after a Claude Code update
Since 2.1.292 local MCP servers default to protocol 2026-07-28. First try `MCP_PROTOCOL_NEGOTIATION=legacy` (env in start-claude), restart, and check the MCP log.
