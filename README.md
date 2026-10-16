# claude-assistant

An always-on personal assistant built on [Claude Code](https://docs.claude.com/en/docs/claude-code/overview):
one long-lived Claude Code session in a Docker container, working for one owner who talks to it over Telegram.
Between messages it wakes itself, works through its own task list, runs scheduled jobs and looks after a
self-hosted home server.

> **This is a personal setup published as a reference, not a turnkey product.** It is shaped around one
> person's server, services and country. Expect to read the code, keep what fits and rewrite the rest.
> It runs Claude Code with permission prompts turned off; read [Security model](#security-model) before you
> run any of it.

Private data (chats, mail, documents, finances) is kept out of the assistant's context by a separate project,
[claude-privacy-vault](https://github.com/s-boris/claude-privacy-vault).

## What it does

- **Chat over Telegram.** A fork of the official Telegram channel plugin (`plugins/telegram-plus`) adds
  streaming drafts, buttons, reactions, polls, rich Markdown, local voice-note transcription and instant
  commands that answer without waking the model.
- **Works between messages.** A heartbeat plugin (`plugins/heartbeat`) wakes the idle session. On each wake it
  reads notes that cron jobs left in `~/inbox`, works through `TODO.md`, and builds toward its goals.
- **Runs scheduled jobs without tokens.** About 50 cron entries do deterministic work (server health, capacity
  forecasts, reminders, price and page watches, bill scans, watchdogs) and only leave a note or send a short
  Telegram message when something needs attention.
- **About 60 command-line tools** in `bin/` (Python and shell), most with an offline test suite in `tests/`.
- **Skills and subagents** in `claude/` package recurring work: media requests, bills, reminders, document
  filing, letters and PDF forms, deadlines, price hunting, link summaries, a weekly digest, and so on.
- **Guard rails as hooks.** `risk-check` rates every shell command, `vault-guard` blocks attempts to reach
  around the privacy vault, `subagent-rules` adds standard rules to every subagent prompt.
- **Versions itself.** Its own config and memory go into a local git history (`brain`), with a secret scan
  on every commit and a daily self-review.

## Architecture at a glance

```
 You (Telegram) ──► telegram-plus ─────┐
 Remote Control / terminal ────────────┤
 heartbeat plugin (idle wake-ups) ─────┼──► main Claude Code session ──► subagents
 cron jobs ──► ~/inbox notes ──────────┘     CLAUDE.md, skills, hooks
                                             │
                                             ▼
                                        bin/* tools ──► server (ssh), web, local services
                                        vault client ──► privacy vault (answers, not text)
```

Details, components and data flows: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
How the session behaves day to day: [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Repository layout

| Path | Installed at | What |
|---|---|---|
| `bin/` | `~/bin` | the assistant's CLI tools, on `PATH` |
| `claude/skills/` | `~/.claude/skills` | skills (one `SKILL.md` per tool or workflow) |
| `claude/agents/` | `~/.claude/agents` | subagent definitions |
| `claude/hooks/` | `~/.claude/hooks` | `risk-check`, `vault-guard`, `subagent-rules`, `no-ask`, with tests |
| `claude/settings.json` | `~/.claude/settings.json` | Claude Code settings: model, hooks, permissions |
| `plugins/` | `~/plugins` | local plugin marketplace: `telegram-plus`, `heartbeat` |
| `templates/workspace/` | `~/workspace` | `CLAUDE.md` (operating manual), `TOOLS.md`, `JOBS.md`, empty `TODO.md` / `GOALS.md` / `PROBLEMS.md` / `PROACTIVE.md` |
| `boot.sh` | `~/boot.sh` | idempotent setup run at every container start |
| `crontab` | `~/crontab` | every scheduled job, one commented line each |
| `examples/` | | `site.env.example` (your values for the placeholders), `profile.example.json` |
| `dash/` | | optional static status dashboard |
| `tests/` | | offline test suites, one directory per tool |
| `tools/` | | repo tooling: `run-tests.sh`, `localize.sh`, the CI privacy gate |

Paths inside the scripts are absolute and assume the container user `node` with home `/home/node`, as in the
Claude Code devcontainer.

## Requirements

- A Linux host that runs Docker, and a Debian-based container image with user `node` (passwordless `sudo`),
  cron, tmux, git, jq, curl, Python 3, Node.js and [Bun](https://bun.sh).
- Claude Code with a Claude account that can use channels and Remote Control (the settings pin a minimum
  version; check the Claude Code docs for current availability).
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your own Telegram user id.
- Optional, depending on which parts you keep: a home server reachable as `ssh unraid` (Unraid-style), Emby,
  Radarr/Sonarr/Seerr, paperless-ngx, Firefly III, Home Assistant, whisper.cpp, Chrome on Xvfb, and the
  [privacy vault](https://github.com/s-boris/claude-privacy-vault).

## Setup outline

There is no installer and the container image is not part of this repo. The steps below are the shape of a
setup; [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md#the-container-image-you-provide) lists what the image must
provide.

1. **Build the image.** Start from the Claude Code devcontainer or a similar Debian image. Add the commands the
   scripts expect from the image: `entrypoint`, `tg`, `restart` and `heartbeat` (contract in HOW-IT-WORKS).
2. **Fill in your values.** Copy `examples/site.env.example` to `site.env`, edit it, and run
   `tools/localize.sh site.env` to replace the placeholders (`homesrv`, `example.net`, `Exampletown`, …).
3. **Copy the files into place** following the layout table. Make `bin/*` executable and put `~/bin` first on
   `PATH`.
4. **Telegram.** Save the bot token to `~/.claude/channels/telegram/.env` (mode 0600) and add your user id to
   `allowFrom` in `access.json` (see `plugins/telegram-plus/README.md` and `ACCESS.md`). Install
   `plugins/telegram-plus/ops/managed-settings.json` as `/etc/claude-code/managed-settings.json` so the custom
   channel plugin is allowed; `boot.sh` does this at every start.
5. **Plugins.** In a Claude Code session: add `~/plugins` as a marketplace and install
   `telegram-plus@home-plugins` and `heartbeat@home-plugins`. They stay disabled in `settings.json` and are
   enabled only for the main session by `start-claude`.
6. **Make it yours.** Copy `templates/workspace/` to `~/workspace` and rewrite its `CLAUDE.md` for your situation: it is one owner's operating manual. Remove
   skills, scripts and cron entries for services you don't run.
7. **Cron.** Copy `crontab` to `~/crontab`, trim it, then `crontab ~/crontab`.
8. **Start.** The entrypoint runs `boot.sh`, then `start-claude` in a tmux session. Message your bot.
9. **Test.** `tools/run-tests.sh` runs the offline suites (`--ci` skips the ones that need local runtimes).

## Configuration

Settings live in `~/.claude/settings.json`, credentials in `~/.secrets/` (0600), per-tool settings in
`~/.config/<tool>/`, and most scripts take environment overrides. Every key the code reads is listed in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) (names only; no values are shipped).

## Security model

**Read this before running anything.** The main session runs Claude Code in `bypassPermissions` mode: it executes
shell commands and edits files **without asking**, because nobody watches the terminal. The safety net is:

- **Hooks.** `risk-check` blocks shell commands it rates HIGH (irreversible data loss, printing or sending
  secrets, mail or public posts, force-push to main) until the owner OKs them on Telegram. It is a rule-based
  safety net and **fails open**. `vault-guard` blocks direct access to private data and **fails closed**.
  Neither is a sandbox: a determined or confused model can get around a pattern match.
- **The privacy vault.** Everything the assistant reads is sent to Anthropic's API. Raw private data therefore
  stays in a separate container that returns answers instead of text. Raw text needs the owner's approval in
  a separate Telegram bot.
- **Approvals.** Outward-facing or irreversible actions (spending money, contacting people, public posts,
  writes to the server) are supposed to be asked for first. That rule lives in the prompt and in `risk-check`,
  so it is only as reliable as those two.
- **Container boundary.** The container is the blast radius. Don't mount host paths you can't afford to lose,
  and give it only the credentials it needs.

More, including how to report a vulnerability: [SECURITY.md](SECURITY.md).

## Limitations

- **Built for one person.** Many skills assume a specific self-hosted stack (Unraid-style server reached via
  `ssh unraid`, Emby, the *arr apps and Seerr, paperless-ngx, Firefly III, Home Assistant) and Swiss specifics
  (QR-bills, cantonal tax deadlines, health-insurance premium data, Swiss public-transport routes, letters in
  Swiss format). Several scripts hard-code the Europe/Zurich time zone.
- **Not portable as-is.** Paths are absolute (`/home/node/...`), and the container image, its entrypoint and the
  server-side SSH lockdown used by `unraid-ssh` are not included.
- **Some test fixtures were removed or rewritten** before publishing, so a few tests may be skipped or fail
  outside the original container.
- **Cost.** An always-on session with a heartbeat uses a lot of tokens. Budget for it or lengthen the heartbeat.
- **Mixed languages.** Skill triggers and some messages are in English and German.

## Disclaimer

This is a personal project. It is **not affiliated with, endorsed by or supported by Anthropic**. "Claude" and
"Claude Code" are Anthropic's names for its products. The software is provided **as is, without warranty of any
kind**; you run it at your own risk, including the risk of an autonomous agent taking actions you did not intend.

## License

[Apache-2.0](LICENSE); see also [NOTICE](NOTICE).
`plugins/telegram-plus` is a modified fork of Anthropic's `telegram@claude-plugins-official` plugin (version
0.0.7), which is licensed under Apache-2.0; its license is kept in `plugins/telegram-plus/LICENSE`.

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
