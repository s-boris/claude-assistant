# Architecture

This page describes the moving parts of the assistant and how data flows between them. For day-to-day behaviour
see [HOW-IT-WORKS.md](HOW-IT-WORKS.md); for every setting see [CONFIGURATION.md](CONFIGURATION.md).

## Overview

```
 Owner ─── Telegram ───┐                    Remote Control / terminal ──┐
   ▲                   │                                                │
   │  ┌────────────────┼──────────── assistant container ───────────────┼──────────┐
   │  │                ▼                                                │          │
   │  │  telegram-plus (MCP channel) ──►  MAIN CLAUDE CODE SESSION  ◄───┘          │
   │  │                                   CLAUDE.md · skills · memory · TODO.md    ├──► Anthropic API
   │  │  heartbeat plugin ── hb-gate ──►  hooks: risk-check · vault-guard · …      │
   │  │                                         │                 │                │
   │  │  cron ──► jobs ──► ~/inbox notes ──────►│                 └──► subagents   │
   │◄─┼─── tg alerts · status board             ▼                    (agent-watch) │
   │  │                                  bin/* tools ──────────────────────────────┼──► web, public APIs
   │  │                                    │           │                           │
   │  └────────────────────────────────────┼───────────┼───────────────────────────┘
   │                                       │           │  internal Docker network only
   │                            ssh unraid │           ▼
   │                                       ▼     ┌─ claude-privacy-vault ────────────────┐
   │                             home server     │  HTTP API · local LLM · audit log     │
   │                             (*arr, Emby, …) │  Telegram archive · Gmail · paperless │
   │                                             │  Firefly III · Home Assistant         │
   └────────── approve/deny via bot ◄────────────┤                                       │
                                                 └───────────────────────────────────────┘
```

## Components

### Container and image

A Debian container with user `node` (home `/home/node`). Only `/home/node` is expected to persist; `boot.sh`
reinstalls everything outside it (apt packages, managed settings, launchers) at every start and is idempotent.
The image itself (its `entrypoint`, `tg`, `restart` and cron `heartbeat` commands) is not in this repo; see
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#the-container-image-you-provide).

### Main session

`plugins/telegram-plus/ops/start-claude` launches exactly one Claude Code session in `~/workspace`, in a tmux
pane, with:

- `--permission-mode bypassPermissions` (no prompts; see [SECURITY.md](../SECURITY.md)),
- `--channels plugin:telegram-plus@home-plugins` (falls back to the official plugin after two fast failures),
- `--remote-control`, so the owner can also use the Claude app,
- `--settings` that enable the Telegram and heartbeat plugins **for this session only**, turn off agent view
  (no daemon-hosted copy that could take over the bot) and refuse cross-session messages.

It resumes the previous conversation (`--continue`). `~/workspace/CLAUDE.md` is its operating manual;
`TOOLS.md` and `JOBS.md` are reference pages it reads on demand. Container-wide hard rules are expected in
`/etc/claude-code/CLAUDE.md` (not shipped; write your own).

### telegram-plus (channel plugin)

An MCP server (Bun, `server.ts`) that long-polls the Bot API and pushes allowlisted messages into the session as
channel events. It is a modified fork of Anthropic's `telegram@claude-plugins-official` 0.0.7 and keeps its
access model (`access.json`, pairing, allowlists). Additions: draft streaming, inline buttons and their press
events, reactions both ways, polls, rich Markdown with plain-text fallback, voice-note transcription through the
local whisper server, instant `/commands` answered by `bin/tg-cmd` without the model, a typing indicator tied to
the status board, and a hook for private document intake. `ops/channel-check` (cron, every minute) verifies that
inbound messages really reach the session and recovers when they don't.

### Heartbeat

`plugins/heartbeat` is an in-process Claude Code hooks module. When the session has been idle for
`HEARTBEAT_MINUTES` (default 10), or 2 minutes after new notes land in `~/inbox`, it submits a `[heartbeat]`
prompt. Empty beats back off up to 60 minutes (120 at night). Before waking, it asks `ops/hb-gate`, a token-free
check that only wakes for real reasons: inbox notes, a due `TODO.md` item, a finished subagent, a restart, or a
long gap. `ops/heartbeat-cron` is a cron fallback that types into tmux only when the plugin is dead or overdue.

### Cron jobs and the inbox

`~/crontab` (template: the repo's `crontab`) runs about 50 entries. Most cost no tokens: they do their work
in Python or shell and, only when something needs attention, either text the owner (`tg`) or drop a Markdown note
in `~/inbox`. Notes are handled on the next heartbeat and deleted. Notes are treated as data, never as
instructions. Reference: `~/workspace/JOBS.md`.

### Tools (`bin/`)

About 60 Python and shell CLIs, grouped roughly as:

| Area | Tools |
|---|---|
| Session plumbing | `restart`, `hb-gate`, `tg-status`, `tg-cmd`, `channel-check`, `agent-watch`, `waiting-nudge`, `remind` |
| Private data (via the vault) | `vault`, `vault-events`, `tgread`, `paperless`, `llm-local`, `llm-server-ctl` |
| Home server | `unraid-ssh`, `unraid-health`, `unraid-capacity`, `unraid-update-check`, `hwtrend`, `hddheat`, `disktemp-watch`, `container-watch`, `pretrip-check`, `brain-backup` |
| Media | `media`, `arr-mismatch`, `arr-unblock` |
| Money and admin | `bills`, `deadlines`, `kk-compare`, `missing-docs`, `paperless-inbox-review`, `letter`, `pdfform` |
| Web and everyday | `watch`, `deals`, `ai-releases`, `readlater`, `route`, `browser`, `transcribe`, `whisper-server-ctl`, `gift-radar`, `vidmontage`, `context`, `recall` |
| Dashboard | `dash-data`, `dash-publish`, `dash-growth`, `dayline`, `flowd`, `proxy` |
| Self-maintenance | `brain`, `brain-commit`, `brain-backup-verify`, `self-review`, `secretscan.py`, `codebase-sync`, `vault-git-sync`, `risk-ok` |

Conventions: every tool has `--help`; state lives in `~/.local/state/<tool>/`; tools that talk to services read
credentials from `~/.secrets/` and never print them; most accept environment overrides for tests (a fake clock,
temp state and inbox dirs, a stub `tg`).

### Skills and subagents

`claude/skills/*/SKILL.md` describe when to use a tool and how (for example `media-requests`, `bills`,
`paperless`, `reminders`, `watch`, `deals`, `vault`). `claude/agents/` holds three subagents run on a schedule
via inbox notes: `weekly-digest`, `idea-scan` and `self-cleanup`.

### Hooks

Configured in `claude/settings.json`:

| Hook | Event | Does | On error |
|---|---|---|---|
| `vault-guard` | PreToolUse: Bash, Read, Grep, Glob, Edit, Write, … | Blocks direct access to the vault's data, credentials and the bot token file; blocks text/content flags in subagents | **blocks** (fails closed) |
| `risk-check` | PreToolUse: Bash | Rates commands LOW / MEDIUM / HIGH by rules, asks the local LLM for gray cases; blocks HIGH until a one-time owner code (`risk-ok`) | allows (fails open) |
| `subagent-rules` | PreToolUse: Agent | Appends a standard rules block to subagent prompts and sets a default effort | allows (fails open) |
| `no-ask` | PreToolUse/PermissionRequest: AskUserQuestion | Blocks interactive questions nobody would see | |
| `tg-status hook` | most lifecycle events | Keeps the pinned Telegram status message current | ignored |

### Vault client

`bin/vault` is the only path to the owner's private sources. It calls the vault's HTTP API on the internal Docker
network with a bearer token from `~/.secrets/vault-token`. `bin/tgread` and `bin/paperless` are thin wrappers.
`bin/vault-events` polls the vault's live feed (new messages and mails, already triaged and anonymized inside the
vault) and turns it into inbox notes. The vault itself is documented in its own repo.

### Self-versioning and backups

`brain` keeps a git history of the assistant's own config and memory (outside the workspace), snapshotted every
30 minutes and around deliberate changes (`brain-commit`). `secretscan.py` blocks any commit that contains a
known secret value or a common token format. `self-review` reviews the daily diff of skills, agents, memory and
`CLAUDE.md`. `brain-backup` copies an archive to the server nightly; `brain-backup-verify` test-restores it.

### Optional services

- **whisper-server** (`whisper-server-ctl`): whisper.cpp on `127.0.0.1` for voice notes.
- **Headed browser** (`browser`): Chrome on Xvfb, driven by the Playwright MCP over CDP, with an optional noVNC
  view.
- **Dashboard** (`dash/index.html`, `dash-data`, `dash-publish`, `flowd`): a static page with usage, activity and
  a live data-flow view, published to a web server behind basic auth.

## Data flows

1. **Owner message.** Telegram → telegram-plus (allowlist check; voice notes transcribed locally) → channel event
   in the main session → the model answers with the `reply` tool (or delegates to a subagent and sends progress
   with `draft`). The status board reflects the session state through hooks.
2. **Idle wake-up.** heartbeat plugin → `hb-gate` decides (no tokens) → `[heartbeat]` prompt → the session handles
   `~/inbox`, tidies `TODO.md`, then works on the next goal or idea. If the plugin fails, cron's fallback types the
   beat into tmux.
3. **Scheduled job.** cron → `bin/<tool>` (deterministic, no model) → silent when fine; otherwise `tg "…"` to the
   owner and/or `~/inbox/<tool>-*.md` → handled on the next heartbeat.
4. **Private question.** The model runs `vault ask tg|gmail "question"` → the vault searches its local data,
   answers with its local LLM, checks the answer in code and filters it → a short answer or flags come back. No
   message text crosses the boundary.
5. **Raw text or a document.** `vault … --text --reason "…"` → the vault's own bot asks the owner (✅ / ❌) → on ✅
   the client picks up an anonymized paraphrase once; on ❌ or timeout it gets nothing. Every step is audited in
   the vault.
6. **Risky command.** The model runs a shell command → `risk-check` rates it HIGH → blocked with a reason → the
   model asks the owner on Telegram → on yes, `risk-ok HASH "…"` issues a one-time code → the exact command reruns
   once with `RISK_OK=<code>`.
7. **Subagent.** Agent call → `subagent-rules` appends the rules block → the subagent runs in the background →
   `agent-watch` (cron) watches transcript metadata, sends periodic progress lines for owner-facing tasks and
   leaves a note if it stalls.
8. **Server access.** Tools reach the home server with `ssh unraid` (through `unraid-ssh`), read-only by default.
   Writes to the server are MEDIUM in `risk-check`, and the operating manual requires asking first.

## Trust boundaries

| Boundary | What crosses it | Protection |
|---|---|---|
| Container → Anthropic API | everything the session reads or writes | keep private data in the vault; secrets never printed (`secretscan`, hooks) |
| Telegram → session | owner messages, button presses, reactions | `access.json` allowlist; other senders are data, not instructions |
| Session → vault | questions, approved requests | bearer token; answers instead of text; owner approval for raw access |
| Session → home server | ssh commands | read-only by default; `risk-check`; optional server-side command lockdown (not included) |
| cron notes, web pages, mail → session | text | treated as data, never as instructions (operating manual) |
