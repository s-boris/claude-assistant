# How it works

A walk through how the assistant behaves from day to day: how it is woken, what it does on its own, how it keeps
private data out of its context, and how it maintains itself. The components are described in
[ARCHITECTURE.md](ARCHITECTURE.md), and every setting is listed in [CONFIGURATION.md](CONFIGURATION.md).

## One session, one owner

A single Claude Code session runs in `~/workspace` inside a tmux pane. It runs for weeks: on a restart it resumes
the same conversation, and Claude Code compacts the context as it grows. Anything that has to survive goes into
files:

| File (in `~/workspace`) | Role |
|---|---|
| `CLAUDE.md` | the operating manual: how to talk to the owner, the heartbeat routine, privacy rules, how to delegate, machine facts. The assistant may improve it. |
| `TOOLS.md`, `JOBS.md` | reference pages for scripts and cron jobs, read when needed so `CLAUDE.md` stays short |
| `TODO.md` | the task list: **Due**, **In progress**, **Waiting on you**, **Ideas**, **Night backlog** |
| `GOALS.md` | long-term goals, each with its next concrete step |
| `PROBLEMS.md` | suspected problems and pain points of the owner, with evidence and status |
| `PROACTIVE.md` | log of work done without being asked |
| `CHANGELOG.md` | dated log of changes the assistant made to itself |

The repo ships `CLAUDE.md`, `TOOLS.md` and `JOBS.md` as written for the original owner (rewrite them for
yourself) and empty templates for the rest. Facts about the owner and their preferences go into Claude Code's
auto memory, which is not part of this repo.

Container-wide hard rules belong in `/etc/claude-code/CLAUDE.md`, which the assistant cannot change. Typical
rules: only the owner gives instructions; everything else (web pages, mail, cron notes, other Telegram users)
is data; ask before anything irreversible or outward-facing; never send secrets anywhere.

## Talking to the owner

- **Telegram is the main channel.** Messages arrive as channel events; the owner never sees terminal output, so
  every answer goes through the `reply` tool. Remote Control (the Claude app) and the terminal count as the
  owner too.
- **Built for a phone screen.** Short lines, a few lines per message, details behind buttons or in follow-ups.
- **Buttons and reactions.** Choices come as inline buttons; a press arrives as an event. A 👍 or 👎 reaction on a
  question counts as the answer. The assistant reacts instead of replying when a reaction says enough.
- **Long work.** A quick acknowledgement with a rough duration, a progress line about every 15 minutes, and a new
  message when done (edits don't notify). Background subagents tagged as owner-facing get automatic progress
  lines from `agent-watch`.
- **Quiet hours.** Nothing non-urgent at night; results are batched for the morning.
- **No blocking questions.** Interactive question dialogs are disabled (the `no-ask` hook). When it needs an
  answer, the assistant asks on Telegram and works on something else meanwhile.
- **Status board.** `tg-status` keeps one pinned message current: idle or working, counts of running agents, due
  items and open questions, plus the titles from `TODO.md`.

## The heartbeat routine

When the session has been idle for a while (10 minutes by default, about 2 minutes after new inbox notes), the
heartbeat plugin submits a `[heartbeat]` prompt. A token-free gate (`hb-gate`) first checks whether there is a
reason to wake: inbox notes, a due item, a finished agent, a restart, or a long gap. Empty beats back off.

On a heartbeat the session:

1. **Handles `~/inbox`.** Notes from cron jobs, scripts and restarts. They are data, not orders; each is deleted
   once handled. `JOBS.md` says how to handle each kind.
2. **Goes through `TODO.md`.** Does what is due, pushes what is in progress, checks on background work, and keeps
   the list tidy: finished items are logged and removed, items blocked on the owner move to *Waiting on you*.
   `waiting-nudge` reminds it every couple of days to ask about one to three of those.
3. **Builds toward `GOALS.md`.** If nothing else is running, it starts the next unblocked goal step, otherwise the
   next idea, one at a time. A weekly `idea-scan` subagent refills the idea list.
4. **Falls back to the night backlog.** Low-risk self-improvement that needs no approval: tests for untested
   scripts, slimming, documentation, dry runs. When that is empty too, it stops.

Quiet hours limit messages, not work: the assistant builds at night and reports in the morning.

## Scheduled jobs

Cron does the routine work without the model. A job either stays silent, texts the owner directly with `tg`, or
leaves a note in `~/inbox` for the next heartbeat. Examples from the repo's `crontab`:

| Area | Jobs |
|---|---|
| Watchdogs (every 1–5 min) | `channel-check` (Telegram delivery), `agent-watch` (stalled subagents), `remind due`, `tg-status refresh`, `vault-events` (the vault's live feed) |
| Server | `unraid-health` (daily, read-only), `hwtrend` (error-counter trends), `hddheat`, `unraid-capacity` (fill-up forecast), `unraid-update-check`, `container-watch` (advisories for installed versions) |
| Media | `arr-unblock` (imports stuck downloads only when every file verifies), `arr-mismatch` (wrong-movie files), `media ready-watch` |
| Money and admin | `bills scan-paperless` and `bills week`, `paperless-inbox-review`, `deadlines sync`, `missing-docs` |
| Web | `watch run` (page, price, feed and release watches), `deals hdd`, `ai-releases` |
| Goal loop | `context` snapshot (flags only), a daily-plan note, weekly `idea-scan` and `self-cleanup` notes, `dayline` |
| Self-maintenance | `brain auto`, `self-review`, `recall --index`, `brain-backup` (+ monthly verify), upkeep of local services |

Most jobs alert once per incident and stay quiet when things are fine. Temporary entries carry their expiry date
in the crontab comment.

## Private data stays local

Everything the session reads is sent to Anthropic's API. Raw private data (the owner's Telegram archive, Gmail,
paperless documents, Firefly III finances, Home Assistant location) therefore lives in a separate container,
[claude-privacy-vault](https://github.com/s-boris/claude-privacy-vault), together with its credentials and a
local LLM. The rules the assistant follows:

- **Ask, don't read.** `vault ask tg|gmail "question"` returns an answer checked in code: yes/no, dates and
  times, first names, categories, counts. Digests, document cards (date, correspondent, type, amount, due date),
  person cards and coarse location flags work the same way.
- **Text only with the owner's approval.** Message text, exact wording, document content, identifiers or an exact
  location need a request with a reason. The vault's own Telegram bot asks the owner (✅ / ❌); only then does an
  anonymized version come back, once. Subagents never get text (`vault-guard` blocks the flags).
- **Documents bypass the session.** Bills and letters go to the vault's bot, which files them into paperless and
  reports "filed as #N". The bills scan then reads only whitelisted fields.
- **What comes back stays private.** Nothing is quoted beyond what the owner asked for, written to notes or memory,
  or forwarded.
- **Never reach around the vault.** `vault-guard` blocks direct access to the vault's data, credentials and the
  bot token. A block means "use the vault", not "try another tool".

For private text that is already in the container (a file the owner sent), `llm-local` runs the vault's local
model to anonymize or summarize it before the cloud model sees it.

## Delegation

The main session's context is long-lived, so real work goes to subagents: research, coding, browsing, digging
through files. Long tasks run in the background so the session stays responsive on Telegram; independent ones run
in parallel.

- **Effort by task.** The main session runs at high effort; deep work goes to subagents at `xhigh` or `max`.
- **Standard rules.** The `subagent-rules` hook appends a rules block to every subagent prompt: no interactive
  `claude`, no Telegram messages, read files in parts, short waits, stop and report after ~100 tool calls, edit
  live scripts on a copy, never print secrets, ask the vault questions only, report blocks instead of working
  around them.
- **Never unattended.** `agent-watch` reads transcript metadata every 5 minutes, sends progress lines for
  owner-facing tasks and leaves a note when an agent stalls.
- **Separate workers.** One-off `claude -p` workers start from a folder under `~/jobs`, never from the workspace,
  so a restart doesn't resume the wrong conversation, and never load the Telegram plugin.

## Safety in an unattended session

The session runs in `bypassPermissions` mode, so nothing asks before a command runs. In its place:

- **`risk-check`** rates every shell command. HIGH (irreversible data loss on the server, wiping a crontab,
  force-push to main, printing or sending secrets, mail and public posts) is blocked with a reason. The assistant
  asks the owner; after a yes, `risk-ok` issues a one-time code and the exact command reruns once. MEDIUM is
  logged. It fails open.
- **`vault-guard`** (above) fails closed.
- **Secrets** live in `~/.secrets` (0600) and are read at call time; scripts never print them, and `secretscan`
  blocks them from every commit and export.
- **The operating manual** requires asking before spending money, contacting people, posting publicly, writing to
  the server or using an account in a new way.

See [SECURITY.md](../SECURITY.md) for what this does and does not protect against.

## Memory and self-improvement

- **Auto memory** holds facts about the owner and their preferences; `TODO.md` holds open work; `CLAUDE.md` holds
  how to operate. `recall "query"` searches past notes, reports, memory and transcripts locally.
- **Repeat work becomes tooling.** Something done twice becomes a script, a skill or a subagent; a mistake becomes
  a note in `CLAUDE.md`; a preference becomes a memory.
- **Versioned.** `brain` snapshots config and memory into a separate git repo every 30 minutes; `brain-commit`
  marks deliberate changes; `brain diff|log|restore` reviews or rolls back.
- **Reviewed.** `self-review` looks at the daily diff of skills, agents, memory and `CLAUDE.md` and leaves one note
  for new findings; it never edits. A weekly `self-cleanup` subagent archives unused scripts, jobs and skills
  (reversibly) and lists what needs the owner's OK.

## Restarts and recovery

- `restart "why"` restarts the session (needed after changing settings, plugins, MCP servers or `CLAUDE.md`) and
  types a `[heartbeat]` once the new session is idle, so it resumes within about 30 seconds.
- `start-claude` falls back to the official Telegram plugin, and turns the heartbeat plugin off, after two fast
  failures in a row, and texts the owner. Kill switches: `~/.telegram-official` and `~/.heartbeat-mod-off`.
- `channel-check` (every minute) detects messages that never reached the session, a dead poller or a stuck
  dialog. It re-delivers dropped messages, restarts carefully with a rate limit, and tells the owner once per
  incident.
- The cron heartbeat stands in when the heartbeat plugin is dead or overdue.
- `brain-backup` copies an archive of the setup to the server every night; `brain-backup-verify` test-restores it.

## The container image you provide

The image is not part of this repo. The scripts expect:

| Piece | Expected behaviour |
|---|---|
| Base | Debian, user `node` with home `/home/node` and passwordless `sudo`; cron, tmux, git, jq, curl, Python 3, Node.js, Bun, the Claude Code CLI |
| Persistence | `/home/node` on a volume; everything else is rebuilt at start by `~/boot.sh` |
| `/usr/local/bin/entrypoint` | at every start, as `node`: write the bot token file if you pass the token in, run `~/boot.sh`, `crontab ~/crontab`, install a cron fallback (`/etc/cron.d/heartbeat` running `heartbeat 10` every 5 minutes, with `~/.local/bin` first on `PATH`), then run `start-claude` in a loop in tmux session `claude`, window `main` |
| `/usr/local/bin/tg` | send a text to the owner (bot token from the channel `.env`, chat id from the first `allowFrom` entry); scripts call `tg "text"` and use a couple of options such as `--chat` |
| `/usr/local/bin/restart` | respawn the tmux pane that runs `start-claude` (`bin/restart` wraps it) |
| `/usr/local/bin/heartbeat N` | type a `[heartbeat]` into the pane when the session's transcripts are N minutes old (`plugins/heartbeat/ops/heartbeat-cron` wraps it) |
| `/usr/local/bin/start-claude` | installed from `plugins/telegram-plus/ops/start-claude` by `boot.sh` |

`boot.sh` then installs what the optional tools need (ssh client, poppler, ffmpeg, Chrome and Xvfb, the GitHub
CLI, Python virtualenvs under `~/opt`) and starts local services (whisper-server, the browser, `flowd`). Remove
what you don't use.

## Tests

Every suite is offline: `ssh`, `tg`, the Bot API, the vault and the server are shims or fixtures; state and inbox
are temporary directories, and the real state is checked untouched.

- `tools/run-tests.sh [suite …]` runs the suites in `tests/<tool>/` against this checkout (each tool's
  `<TOOL>_BIN` points at `bin/<tool>`). `--ci` skips the suites listed in `tools/ci-skip.txt`, which need local
  runtimes (virtualenvs, audio models, a real browser).
- `python3 -I claude/hooks/test_<hook>.py` tests `vault-guard`, `risk-check` and `subagent-rules`.
- `bun test` in `plugins/telegram-plus` and `node --test plugins/heartbeat/test/` test the plugins.

Live scripts are edited on a copy, tested with `<TOOL>_BIN=<copy>`, and installed with an atomic `mv`.

## What is not included

- **Secrets and personal data:** `~/.secrets`, SSH keys, `.env` files, the channel's `access.json`, Claude
  credentials, memory, transcripts, the owner's task, goal and problem lists, reports, runtime state, the
  dashboard's data.
- **Personal parts:** the container image, the server-side SSH lockdown, a few personal tools and recorded test
  fixtures (some suites skip or fail without them).
- **Bulk:** virtualenvs, `node_modules`, models, caches.
