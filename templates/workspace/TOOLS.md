# My scripts (reference)

<!-- Template. One line per script in ~/bin: what it does and how to call it. Keep it current when you add or
     retire a script; the details belong in the script's own --help and its skill. Delete what you don't use. -->

Every script prints usage with `--help`. Tests: `tests/<name>/` (run with `tools/run-tests.sh <name>`).

## Everyday
- `remind add|list|cancel` — durable reminders; owner reminders go to Telegram at the exact minute.
- `watch` — web watches checked by cron; notify only on a change or threshold.
- `readlater URL` — fetch and extract an article, video transcript, podcast or PDF for a summary.
- `route` — public transport and driving times (`route FROM TO [--at HH:MM]`).
- `transcribe FILE` / `whisper-server-ctl` — local speech-to-text.
- `context` — the daily context snapshot (calendar, flags, location band) for the heartbeat.
- `recall "query"` — search past sessions, decisions and work.

## Private data (optional: claude-privacy-vault)
- `vault …` — the only door to the vault: `vault ask tg|gmail "question"`, `vault facts`, `vault plans`,
  `vault gmail get ID` (a card), `vault ha now`. Raw text and files only with the owner's approval.
- `tgread …` — alias for `vault tg …` (Telegram archive inside the vault).
- `paperless …` — document cards and filing through the vault.
- `vault-events` — the live feed of new private events; writes ~/inbox notes.
- `llm-local` / `llm-server-ctl` — a local model for private text that must not reach a cloud API.

## Server and media (optional: a home server reachable as `ssh unraid`)
- `unraid-health`, `unraid-capacity`, `hwtrend`, `hddheat`, `disktemp-watch`, `unraid-update-check` — read-only
  health, capacity forecast, disk trends and update checks.
- `container-watch` — container inventory vs. upstream releases and security advisories.
- `media` — Seerr / Radarr / Sonarr / Emby requests and status; `arr-mismatch`, `arr-unblock` — queue fixes.
- `proxy`, `flowd`, `dash-publish`, `dash-data`, `dash-growth` — reverse-proxy helpers and the status dashboard.

## Money and admin (Switzerland-specific; adapt or delete)
- `bills` — bill tracking with due-date reminders; `paperless-inbox-review`, `missing-docs`.
- `deadlines` — yearly money/admin deadline radar from `~/.config/deadlines/ch-xx.json` (your canton's data).
- `kk-compare` — health-insurance premium comparison from the official BAG data.
- `letter`, `pdfform` — print-ready letters and PDF form filling; `deals` — price tracking (read-only).

## Status and dashboard
- `tg-status` — the pinned Telegram status board from TODO.md; `tg-cmd` — bot commands.
- `dayline` — one-line day summary; `waiting-nudge` — reminds you to clear "Waiting on you".

## Self-maintenance
- `brain`, `brain-commit`, `brain-backup`, `brain-backup-verify` — versioned config and memory.
- `self-review` — nightly review of changes to skills, agents, memory and CLAUDE.md.
- `secretscan.py` — blocks commits and messages that contain secrets (`--redact` to mask).
- `codebase-sync`, `vault-git-sync` — sanitized exports to private git repos.
- `agent-watch` — watches background subagents and reports stalls.

## Session plumbing
- `restart "why"` — reload the session (settings, plugins, MCP servers, CLAUDE.md); resumes on the next heartbeat.
- `hb-gate` — decides whether a heartbeat fires (skips while background agents run; wakes for due items).
- `channel-check` — cron check that Telegram inbound still works; texts the owner when it doesn't.
- `risk-ok HASH "owner's words"` — one-time approval for a command the risk-check hook blocked.
- `browser` — the headed Chrome (Xvfb) behind the `browser` MCP server.
