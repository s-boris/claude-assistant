# Configuration

Everything the code reads, by key **name**. No values are shipped: you create the files below yourself.
Paths assume the container user `node` (home `/home/node`, written `~`).

## Where configuration lives

| Place | Holds | Notes |
|---|---|---|
| `~/.config/assistant/profile.json` | your personal values shared by many tools: name, home, server, LAN, domain | see [Profile](#profile-configassistantprofilejson); template `examples/profile.example.json` |
| `examples/site.env.example` → `site.env` | your values for the placeholders in this repo | apply with `tools/localize.sh site.env` |
| `~/.claude/settings.json` | Claude Code settings: model, effort, hooks, permissions, plugins | from `claude/settings.json` |
| `/etc/claude-code/managed-settings.json` | allows the custom channel plugin | from `plugins/telegram-plus/ops/`; `boot.sh` installs it |
| `/etc/claude-code/CLAUDE.md` | your container-wide hard rules for every session | not shipped; write your own |
| `~/.claude/channels/telegram/` | bot token, access list, instant commands | see [Telegram](#telegram-channel-files) |
| `~/.secrets/` | credentials, one file each, mode 0600, directory 0700 | see [Secrets](#secrets-secrets) |
| `~/.config/<tool>/` | per-tool settings and switches | see [Per-tool config](#per-tool-config-config) |
| `~/.ssh/config` | the `unraid` host alias the server tools use | see [SSH](#ssh) |
| `~/crontab` | all scheduled jobs | from the repo's `crontab`; then `crontab ~/crontab` |
| environment | overrides, mostly for tests | see [Environment variables](#environment-variables) |
| `~/.local/state/<tool>/` | runtime state and logs | created by the tools; not configuration |

### Placeholders

The published code uses placeholders where the original setup had personal values: the server name
`homesrv` / `Homesrv`, the domain `example.net`, the documentation address ranges `198.51.100.x` and
`203.0.113.x`, the owner `Alex` / `owner@example.com`, the Telegram id `100000001`, the town `Exampletown` and
the street `Examplestrasse 1`.
`examples/site.env.example` lists them (`SERVER_NAME`, `PUBLIC_DOMAIN`, `LAN_PREFIX`, `OWNER_FIRST_NAME`,
`OWNER_EMAIL`, `TELEGRAM_CHAT_ID`, `HOME_TOWN`); `tools/localize.sh` rewrites them in place (tests keep the
placeholders).

## Profile (`~/.config/assistant/profile.json`)

One private JSON file holds the personal values that many tools share, so code never hard-codes them. Copy
`examples/profile.example.json`, `chmod 600` it, and fill in what applies; keys you leave out keep the public
placeholder defaults (the values in the example). Tools are moving to it step by step; until a tool reads a
key, the placeholder in its code still applies (see Placeholders above).

| Section | Keys |
|---|---|
| `owner` | `first_name`, `last_name`, `email`, `phone`, `birth_year`, `github` |
| `home` | `street`, `postcode`, `town`, `town_aliases` (other spellings), `canton`, `municipality_bfs`, `lat`, `lon` |
| `work` | `town`, `lat`, `lon` |
| `locale` | `tz`, `lang`, `country` |
| `network` | `server_name`, `server_host`, `lan_prefix` (first three octets plus the dot), `domain`, `hosts` (free-form name → `user@host`, e.g. `web_vm`) |
| `services` | `seerr_user`, `ha_user` (your Home Assistant person), `ha_url` (Home Assistant base URL), `ha_phone` (the HA device slug of your phone, e.g. `pixel_8`), `ha_keep_models[]` (phone model codes a cleanup must never touch) |
| `repos` | `private_code`, `private_vault`, `public_code`, `public_vault` (`owner/name`) |
| `interests` | `topics[]`, `events[]` |

- Python: `sys.path.insert(0, <bin>/lib)`, then `import assistant_profile as profile; profile.get("home.town")`.
  A missing file means defaults; a file that is unreadable, not JSON or has a value of the wrong type raises
  `ProfileError` (the message names the key, never the value).
- Shell: `town=$(profile-get home.town) || exit 1`. `profile-get KEY --hash` prints the sha256 of the value
  (compare without showing it), `--keys` lists the key names, `--check` validates the file (types, unknown
  keys, mode 600), `--source` says which file is in use.
- `ASSISTANT_PROFILE=/path/to/file` reads another file. `tools/run-tests.sh` sets it to the synthetic
  `tests/profile/fixtures/profile.json` for every suite, so tests never see your real values.
- Your Telegram id stays in `access.json` (`allowFrom`); tool-specific settings stay in `~/.config/<tool>/`.
- The hooks (`risk-check`, `vault-guard`) read `owner.github` and `network.*` themselves and fail closed: without
  a usable profile every host counts as one of yours, no secret upload gets the owner-host relief, and no
  GitHub account counts as yours, so a broken profile can only add blocks.

## Telegram channel files

In `~/.claude/channels/telegram/` (the same state directory as the official plugin):

| File | Contents |
|---|---|
| `.env` | `TELEGRAM_BOT_TOKEN=…` (mode 0600). `start-claude` keeps the token out of the session's environment; the plugin, `tg` and `tg-status` read this file when they need it. `vault-guard` blocks the model from reading it. |
| `access.json` | `dmPolicy`, `allowFrom` (your Telegram user id first: scripts that message you use the first entry), groups, `replyToMode`, `forwardReactions`, … See `plugins/telegram-plus/ACCESS.md`. |
| `instant.json` | instant `/commands` that `bin/tg-cmd` answers without waking the model |
| `inbox/` | downloaded attachments (a cron entry prunes files older than 7 days) |

## Secrets (`~/.secrets/`)

Each file is read at call time and never printed. `secretscan.py` loads these values to block them from commits
and exports. Create only the ones for services you use.

| File | Format (key names) | Read by |
|---|---|---|
| `vault-token` | bare token (the vault stores only its sha256) | `vault` and everything that goes through it |
| `vault-test-token` | bare token for a throwaway test vault | `vault` (test instance) |
| `media.json` | `{"seerr": {"url","key"}, "radarr": {"url","key"}, "sonarr": {"url","key"}}`, optional `"tracearr": {"url","key"}` | `media`, `arr-mismatch`, `arr-unblock`, `container-watch`, `unraid-capacity` |
| `tracearr-key` | bare key (alternative to the `tracearr` entry) | `media` |
| `paperless.json` | `{"url","token"}`, legacy direct access | `container-watch` (version check). With vault mode on, paperless is reached only through the vault and `vault-guard` blocks this file. |
| `novnc.txt` | VNC password for `browser view` | `browser` |
| `browser-basicauth.txt` | basic-auth credentials for the dashboard / remote-browser vhost | `flowd`, `browser` skill |

Also outside `~/.secrets`: SSH keys in `~/.ssh`, the GitHub CLI login (`gh auth`) if you use `vault-git-sync` or
`codebase-sync`, and Claude Code's own credentials.

## Per-tool config (`~/.config/`)

| Path | Purpose |
|---|---|
| `vault/url` | vault base URL (default `http://claude-vault:8300`) |
| `vault/mode` | `on` = paperless only through the vault (also turns on the matching `vault-guard` rule) |
| `vault-events/ha` | `off` skips Home Assistant events from the vault feed |
| `llm-local/backend` | `vault` = run `llm-local` on the vault's model; otherwise a local llama-server |
| `llm-local/server.env` | llama-server settings for `llm-server-ctl` (same names as its `LLM_LOCAL_*` variables) |
| `transcribe/vocab.txt` | names and terms passed to Whisper as the initial prompt |
| `hb-gate/off` | file present = the heartbeat gate always wakes |
| `unraid-health/ack.json` | acknowledged server issues: match, expiry, reason |
| `unraid-lockdown/mode` | `root`, `verbs`, `emulate` or `auto`: how `unraid-ssh` reaches the server |
| `arr-mismatch/no-drop` | file present = report wrong-movie files, never remove them |
| `deadlines/ch-xx.json` | yearly deadline data with official sources (`xx` = your canton code; write your own canton's file) |
| `deadlines/profile.json` | facts that switch deadline items on or off |
| `missing-docs/expected.json` | documents you expect in paperless each year (format in its `_format` key) |
| `kk/profile.json` | health-insurance comparison defaults: canton, premium region, municipality, age group, accident cover |
| `letter/sender.json` | sender block for letters |
| `letter/signature.png` | signature image for `letter` and `pdfform` |
| `deals/config.json` | HDD price alert: `hdd_alert` with `min_tb`, `chf_per_tb`, `basis`, `notify` |
| `dash/dataflow.json` | the curated data map for the dashboard's Architecture tab |
| `codebase-sync/scrub.txt`, `codebase-sync/off` | private replace rules and off switch for `codebase-sync` |

## SSH

Server tools run `ssh unraid …` through `bin/unraid-ssh`. Define the alias in `~/.ssh/config` (host, user, key).
`unraid-ssh` supports a server-side command allowlist ("lockdown"); that server part is not included, so use mode
`root` (plain ssh) or write your own dispatcher. The vault's admin commands use the same alias
(`VAULT_SSH_HOST`).

## Claude Code settings

`claude/settings.json` sets:

- `model`, `effortLevel`, `autoCompactWindow`, `minimumVersion`.
- `permissions.defaultMode: "bypassPermissions"` and a small `deny` list (interactive questions, plan mode, the
  browser's unsafe code tool). See [SECURITY.md](../SECURITY.md).
- `hooks`: `vault-guard` (with `"onFailure": "block"`), `risk-check`, `subagent-rules`, `no-ask`, and `tg-status`
  on most lifecycle events.
- `enabledPlugins`: both home plugins **off**. `start-claude` turns them on for the main session only, through
  `--settings`. A second session that loads the Telegram plugin would take over the bot's connection.
- `extraKnownMarketplaces.home-plugins`: the local marketplace in `~/plugins`.
- `env`: a few switches that turn off feedback surveys and error reporting.

**Do not set** `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `DISABLE_GROWTHBOOK` or
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`. They also turn off feature flags; Claude Code then reports that
channels are unavailable and Telegram messages are silently dropped. `channel-check` detects this.

Don't turn agent view off globally (`CLAUDE_CODE_DISABLE_AGENT_VIEW` or user/managed settings): cron jobs and the
restart helper rely on `claude agents --json`. `start-claude` disables it only for the main session.

## Environment variables

Most variables exist so tests can point a tool at fixtures. Common patterns:

| Pattern | Meaning |
|---|---|
| `<TOOL>_NOW` / `<TOOL>_TODAY` | fake clock for tests |
| `<TOOL>_STATE`, `<TOOL>_STATE_DIR` | state directory (default `~/.local/state/<tool>`) |
| `<TOOL>_INBOX`, `<TOOL>_INBOX_DIR` | where notes go (default `~/inbox`) |
| `<TOOL>_TG` | command used to text the owner (default `tg`) |
| `<TOOL>_HOST` | ssh host alias (default `unraid`) |
| `<TOOL>_BIN`, `<TOOL>_VAULT`, `<TOOL>_PAPERLESS`, `<TOOL>_REMIND` | path of a helper tool, so tests can stub it |
| `<TOOL>_NO_SEND` | `1` = never message, just report |

### Session, plugins and hooks

| Variable | Read by | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | telegram-plus | bot token; normally taken from the channel `.env` file, kept out of the session env |
| `TELEGRAM_STATE_DIR` | telegram-plus | channel state dir (default `~/.claude/channels/telegram`, honours `CLAUDE_CONFIG_DIR`) |
| `TELEGRAM_ACCESS_MODE` | telegram-plus | `static` = read `access.json` once at start (as in the official plugin) |
| `TELEGRAM_API_ROOT` | telegram-plus | Bot API base URL (tests use a mock) |
| `TELEGRAM_NO_POLL`, `TELEGRAM_FORCE_POLL` | telegram-plus | `1` = never poll / always poll |
| `TELEGRAM_QUIET_HOURS` | telegram-plus | quiet-hours window (default `0-8`) |
| `TELEGRAM_INSTANT_FILE`, `TELEGRAM_INSTANT_TIMEOUT_MS` | telegram-plus | instant-commands config and timeout |
| `TELEGRAM_SCAN_PRIVATE_CMD`, `TELEGRAM_SCAN_PRIVATE_TIMEOUT_MS` | telegram-plus | paperless client for `/scan private` (files a document without the session seeing it), and its per-upload timeout |
| `TELEGRAM_STATUS_STATE`, `TELEGRAM_STATUS_TIMEOUT_MS` | telegram-plus | status-board state file (from `tg-status`) and timeout |
| `TELEGRAM_TRANSCRIBE_TIMEOUT_MS`, `TELEGRAM_TRANSCRIPT_KEEP_MS` | telegram-plus | voice-note transcription timeout and cache lifetime |
| `TELEGRAM_MCP_LOG_DIR` | telegram-plus | MCP log directory (default under `XDG_CACHE_HOME`) |
| `TELEGRAM_ACK_DELAY_MS`, `TELEGRAM_GATE_RECHECK_MS`, `TELEGRAM_HEALTH_POLL_MS`, `TELEGRAM_MARKER_WAIT_MS`, `TELEGRAM_POLL_CHECK_MS`, `TELEGRAM_REDELIVER_MS`, `TELEGRAM_REPLAY_CHECK_MS`, `TELEGRAM_VERDICT_WAIT_MS` | telegram-plus | delivery, health-check and redelivery timings |
| `TELEGRAM_TYPING_GRACE_MS`, `TELEGRAM_TYPING_MAX_MS`, `TELEGRAM_TYPING_NO_BOARD_MS`, `TELEGRAM_TYPING_REFRESH_MS`, `TELEGRAM_TYPING_TRACE` | telegram-plus | "typing…" indicator timings and trace |
| `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CONFIG_DIR`, `XDG_CACHE_HOME` | telegram-plus | provided by Claude Code / the OS; read, not set |
| `HEARTBEAT_MINUTES` | heartbeat plugin | idle minutes before a heartbeat (default 10, minimum 3) |
| `HEARTBEAT_GATE` | heartbeat plugin | `off` = skip `hb-gate` |
| `HEARTBEAT_MOD_TEST_DIR` | heartbeat plugin | tests only: fast clock and private dirs |
| `HEARTBEAT_REAL`, `HEARTBEAT_MOD_STATE`, `HEARTBEAT_PROJECT`, `HEARTBEAT_INBOX`, `HEARTBEAT_MARKER`, `HEARTBEAT_NOW` | `heartbeat-cron` | the image's heartbeat command, plugin state file, transcript dir, inbox, last-beat marker, clock |
| `RESTART_REAL`, `RESTART_MARKER` | `restart` | the image's restart command; last-beat marker |
| `RISK_OK` | `risk-check` | `RISK_OK=<code> <command>` runs one blocked command once after the owner's OK (`risk-ok`) |
| `RISK_CHECK_TIMEOUT`, `RISK_CHECK_LOG`, `RISK_CHECK_STATE` | `risk-check` | wrapper timeout (default 12 s), log file, state dir |
| `RISK_CHECK_LLM`, `RISK_CHECK_LLM_CMD`, `RISK_CHECK_LLM_TIMEOUT` | `risk-check` | `off` = rules only; LLM command (default `llm-local`); timeout (default 8 s) |
| `SUBAGENT_RULES_LOG`, `SUBAGENT_RULES_TIMEOUT`, `SUBAGENT_RULES_AGENTS_DIR` | `subagent-rules` | log, timeout, agent definitions dir |
| `VAULT_MODE` | `vault-guard`, `vault`, `bills` | `on` = paperless only through the vault (else `~/.config/vault/mode`) |
| `VAULT_GUARD_HOME` | `vault-guard` | home directory to protect (tests) |
| `CLAUDE_JOB_DIR` | `tg-status` | provided by Claude Code for background sessions; read, not set |

### Privacy vault client and local models

| Variable | Read by | Purpose |
|---|---|---|
| `VAULT_URL`, `VAULT_TOKEN_FILE` | `vault` | API URL (else `~/.config/vault/url`) and token file (else `~/.secrets/vault-token`) |
| `VAULT_SSH_HOST` | `vault` | ssh alias for `vault admin` (default `unraid`) |
| `VAULT_QUIET` | `vault` | `1` = no progress lines on long calls |
| `VAULT_GIT_SYNC` | `vault` | path of `vault-git-sync` |
| `VAULT_GIT_DIR`, `VAULT_GIT_SRC_BIN`, `VAULT_GIT_SRC_OPT`, `CRON` | `vault-git-sync` | the vault repo checkout and the source dirs it mirrors; `CRON=1` = report failures as inbox notes |
| `VAULT_EVENTS_CLIENT`, `VAULT_EVENTS_BILLS`, `VAULT_EVENTS_BILLS_WAIT`, `VAULT_EVENTS_HA_SWITCH`, `VAULT_EVENTS_INBOX`, `VAULT_EVENTS_NOW` | `vault-events` | client and `bills` paths, wait before the bills scan, HA switch file, inbox, clock |
| `LLM_LOCAL_BACKEND`, `LLM_LOCAL_VAULT_BIN` | `llm-local` | `vault` = use the vault's model; vault client path |
| `LLM_LOCAL_PORT`, `LLM_LOCAL_CTL`, `LLM_LOCAL_CACHE_DIR`, `LLM_LOCAL_STATS_FILE`, `LLM_LOCAL_TICKET` | `llm-local` | local server port, its control script, cache, stats file and scheduling ticket (the last two are used inside the vault) |
| `LLM_LOCAL_BIN`, `LLM_LOCAL_SRC`, `LLM_LOCAL_MODEL`, `LLM_LOCAL_CONF`, `LLM_LOCAL_CTX`, `LLM_LOCAL_THREADS`, `LLM_LOCAL_THREADS_BATCH`, `LLM_LOCAL_SPEC`, `LLM_LOCAL_DRAFT`, `LLM_LOCAL_DRAFT_MAX`, `LLM_LOCAL_MIN_FREE_MB` | `llm-server-ctl` | llama.cpp binary and source, model file, settings file, context size, threads, speculative decoding, minimum free RAM |
| `TRANSCRIBE_MODEL`, `TRANSCRIBE_LANGS`, `TRANSCRIBE_THREADS`, `TRANSCRIBE_VOCAB`, `TRANSCRIBE_SERVER`, `TRANSCRIBE_SERVER_PORT`, `TRANSCRIBE_DEBUG` | `transcribe`, `whisper-server-ctl` | Whisper model, languages, threads, vocabulary file, server URL (`off` = CLI only), port, debug |

### Home server

| Variable | Read by | Purpose |
|---|---|---|
| `UNRAID_SSH_HOST` | `unraid-ssh` | ssh alias (default `unraid`) |
| `UNRAID_SSH_BIN` | most server tools | path of `unraid-ssh` |
| `UNRAID_LOCKDOWN_MODE`, `UNRAID_LOCKDOWN_CONF`, `UNRAID_LOCKDOWN_STATE`, `UNRAID_LOCKDOWN_DISPATCHER` | `unraid-ssh` | lockdown mode (else `~/.config/unraid-lockdown/mode`), its config, state and local dispatcher copy |
| `UNRAID_HEALTH_HOST`, `UNRAID_HEALTH_STATE_DIR`, `UNRAID_HEALTH_INBOX_DIR`, `UNRAID_HEALTH_TG`, `UNRAID_HEALTH_NO_SEND`, `UNRAID_HEALTH_REPORT_DIR`, `UNRAID_HEALTH_ACK_FILE`, `UNRAID_HEALTH_HDDHEAT_LOG`, `UNRAID_HEALTH_TZ` | `unraid-health` | host, dirs, sender, report dir, acknowledged issues, heat log, time zone |
| `UNRAID_CAPACITY_HOST`, `UNRAID_CAPACITY_STATE_DIR`, `UNRAID_CAPACITY_INBOX_DIR`, `UNRAID_CAPACITY_TG`, `UNRAID_CAPACITY_NO_SEND`, `UNRAID_CAPACITY_SHARE`, `UNRAID_CAPACITY_SSH_RETRY_WAIT` | `unraid-capacity` | as above, plus the share whose allocation models the disks (default `media`) and ssh retry wait |
| `HWTREND_HOST`, `HWTREND_STATE_DIR`, `HWTREND_INBOX_DIR`, `HWTREND_UH` | `hwtrend` | host, dirs, path of `unraid-health` (it reuses its collector) |
| `HDDHEAT_HOST`, `HDDHEAT_STATE_DIR`, `HDDHEAT_INBOX_DIR`, `HDDHEAT_TG`, `HDDHEAT_NOW` | `hddheat` | |
| `LIMIT` | `disktemp-watch` | temperature threshold in °C |
| `CONTAINER_WATCH_HOST`, `CONTAINER_WATCH_TG` | `container-watch` | |
| `PRETRIP_HOST`, `PRETRIP_BIN_DIR`, `PRETRIP_CRONTAB`, `PRETRIP_NOW` | `pretrip-check` | host, tools dir, crontab to check, clock (also reads `REMIND_DB`, `HWTREND_STATE_DIR`, `UNRAID_HEALTH_STATE_DIR`) |
| `KEEP` | `brain-backup` | days of archives to keep |
| `BRAIN_BACKUP_LIVE` | `brain-backup-verify` | live tree to compare a restore against |

### Media

| Variable | Read by | Purpose |
|---|---|---|
| `MEDIA_SECRETS`, `MEDIA_TRACEARR_KEYFILE` | `media`, `arr-unblock` | paths of `media.json` and the Tracearr key |
| `MEDIA_EMBY_DIR`, `MEDIA_EMBY_SQL` | `media` | Emby data dir and the command that runs read-only SQL on its library |
| `MEDIA_REPORT_DIR`, `MEDIA_NOW` | `media` | reports dir, clock |
| `MEDIA_READY_STATE`, `MEDIA_READY_USER`, `MEDIA_READY_TG`, `MEDIA_READY_INBOX`, `MEDIA_READY_QUIET` | `media` (ready-watch) | state, Seerr user, sender, inbox, quiet hours (default `0-8`) |
| `ARR_MISMATCH_STATE`, `ARR_MISMATCH_GRABS_STATE`, `ARR_MISMATCH_DROPS_LOG`, `ARR_MISMATCH_INBOX`, `ARR_MISMATCH_NO_DROP` | `arr-mismatch` | state, grab history, removal log, inbox, report-only switch |
| `ARR_UNBLOCK_STATE`, `ARR_UNBLOCK_LOG`, `ARR_UNBLOCK_INBOX`, `ARR_UNBLOCK_CMD_WAIT`, `ARR_UNBLOCK_QUEUE_WAIT` | `arr-unblock` | state, log, inbox, waits for *arr commands and queue refresh |

### Money and admin

| Variable | Read by | Purpose |
|---|---|---|
| `BILLS_STATE_DIR`, `BILLS_INBOX`, `BILLS_REMIND`, `BILLS_PAPERLESS`, `BILLS_VAULT`, `BILLS_NOW`, `BILLS_NO_QR` | `bills` | state, inbox, helper paths, clock; `BILLS_NO_QR` = tests of the text-only path |
| `DEADLINES_DATA`, `DEADLINES_PROFILE`, `DEADLINES_STATE`, `DEADLINES_PAPERLESS`, `DEADLINES_REMIND`, `DEADLINES_NOW`, `DEADLINES_TODAY` | `deadlines` | data and profile files (else `~/.config/deadlines/`), state, helpers, clock |
| `KK_DATA_DIR`, `KK_PROFILE`, `KK_OFFLINE` | `kk-compare` | premium data dir, profile, `1` = never download |
| `MDOC_CONFIG`, `MDOC_PROFILE`, `MDOC_STATE_DIR`, `MDOC_INBOX`, `MDOC_PAPERLESS`, `MDOC_REMIND`, `MDOC_NOW` | `missing-docs` | |
| `PINB_STATE_DIR`, `PINB_INBOX`, `PINB_PAPERLESS`, `PINB_NOW` | `paperless-inbox-review` | |
| `ADMIN_DESK_PAPERLESS`, `ADMIN_DESK_REMIND`, `ADMIN_DESK_NOW`, `LETTER_CONFIG_DIR`, `LETTER_SIGNATURE` | `letter`, `pdfform` | helpers, clock, config dir (else `~/.config/letter`), signature image |

### Web, everyday and agents

| Variable | Read by | Purpose |
|---|---|---|
| `WATCH_STATE_DIR`, `WATCH_INBOX`, `WATCH_TG`, `WATCH_NOW`, `WATCH_TIMEOUT`, `WATCH_HOST_GAP` | `watch` | dirs, sender, clock, HTTP timeout, minimum seconds between requests to one host (default 900) |
| `WATCH_GITHUB_BASE`, `WATCH_COINGECKO_BASE`, `WATCH_FRANKFURTER_BASE`, `WATCH_YAHOO_BASE` | `watch` | API base URLs (tests) |
| `DEALS_CONFIG`, `DEALS_STATE_DIR`, `DEALS_PAGE_DELAY`, `DEALS_SAVE_RAW`, `DEALS_ALTERNATE_BASE`, `DEALS_MEDIAMARKT_BASE` | `deals` | config, state, delay between pages, save fetched pages as fixtures, shop base URLs (tests) |
| `AI_RELEASES_SOURCES`, `AI_RELEASES_DAILY_CAP`, `AI_RELEASES_JUDGE`, `AI_RELEASES_FIXTURES`, `AI_RELEASES_STATE_DIR`, `AI_RELEASES_INBOX`, `AI_RELEASES_TG`, `AI_RELEASES_NOW` | `ai-releases` | source list, alerts per day, judge command (prompt on stdin, JSON out), fixture dir, dirs, sender, clock |
| `READLATER_HOME`, `READLATER_TIMEOUT`, `READLATER_CHUNK_SECONDS`, `READLATER_HOST_MAP`, `READLATER_YTDLP`, `READLATER_TRANSCRIBE`, `READLATER_PIP`, `READLATER_INBOX` | `readlater` | home, timeouts, audio chunk length, `host=host` rewrites, tool paths, inbox |
| `ROUTE_API` | `route` | public-transport API base URL |
| `BROWSER_CHROME`, `BROWSER_DISPLAY`, `BROWSER_SIZE`, `BROWSER_CDP_PORT`, `BROWSER_VNC_PORT`, `BROWSER_VIEW_PORT` | `browser` | Chrome binary, X display, window size, ports |
| `VIDMONTAGE_FONT` | `vidmontage` | font for titles |
| `REMIND_DB`, `REMIND_INBOX`, `REMIND_TG`, `REMIND_NOW` | `remind` (also `gift-radar`, `pretrip-check`) | reminder database, inbox, sender, clock |
| `GIFT_RADAR_MEMORY`, `GIFT_RADAR_VAULT`, `GIFT_RADAR_REMIND`, `GIFT_RADAR_REPORTS`, `GIFT_RADAR_STATE`, `GIFT_RADAR_INBOX` | `gift-radar` | birthday list file, helpers, dirs |
| `CONTEXT_STATE`, `CONTEXT_NOW`, `CONTEXT_UNRAID_REPORTS`, `CONTEXT_TG_TIMEOUT`, `CONTEXT_WEATHER_URL`, `CONTEXT_METEO_URL`, `CONTEXT_HN_BASE` | `context` | state, clock, server reports dir, vault digest timeout, weather and news URLs |
| `AGENT_WATCH_PROJECT`, `AGENT_WATCH_STATE_DIR`, `AGENT_WATCH_INBOX`, `AGENT_WATCH_TG`, `AGENT_WATCH_NOW`, `AGENT_WATCH_STALL_MIN`, `AGENT_WATCH_PROGRESS_MIN`, `AGENT_WATCH_WINDOW_MIN` | `agent-watch` | transcript dir, dirs, sender, clock, stall threshold, progress interval, look-back window |
| `WAITING_NUDGE_TODO`, `WAITING_NUDGE_STATE`, `WAITING_NUDGE_INBOX`, `WAITING_NUDGE_TODAY` | `waiting-nudge` | |
| `TG_STATUS_DIR`, `TG_STATUS_TODO`, `TG_STATUS_DEBOUNCE`, `TG_STATUS_TRACE`, `TG_STATUS_API`, `TG_STATUS_TEST_TOKEN`, `TG_STATUS_TEST_CHAT` | `tg-status` | state, `TODO.md` path, debounce seconds, trace, mock API with test token and chat |
| `TG_CMD_BIN`, `TG_CMD_NOW` | `tg-cmd` | |

### Self-maintenance and dashboard

| Variable | Read by | Purpose |
|---|---|---|
| `BRAIN_GIT_DIR`, `BRAIN_WORK_TREE`, `BRAIN_INBOX` | `brain`, `brain-commit`, `self-review`, `brain-backup-verify` | the self-versioning repo and its work tree |
| `SELF_REVIEW_BIN`, `SELF_REVIEW_LLM`, `SELF_REVIEW_HARD_RULES`, `SELF_REVIEW_EXTRA_SRC`, `SELF_REVIEW_REPORTS`, `SELF_REVIEW_STATE`, `SELF_REVIEW_INBOX` | `self-review` | tools dir, LLM command, hard-rules file (default `/etc/claude-code/CLAUDE.md`), extra source dirs, dirs |
| `SECRETSCAN_SOURCES` | `secretscan.py` | read secret values from this dir instead of `~` (tests) |
| `RECALL_HOME`, `RECALL_STATE`, `RECALL_NOW`, `RECALL_SECRETSCAN_DIR` | `recall` | |
| `CODEBASE_SYNC_HOME`, `CODEBASE_SYNC_EXPORT`, `CODEBASE_SYNC_SCRUB`, `CODEBASE_SYNC_STATE`, `CODEBASE_SYNC_INBOX` | `codebase-sync` | source home, export checkout, scrub rules, dirs |
| `DASH_WORKSPACE`, `DASH_STATE`, `DASH_PROJECTS`, `DASH_JOBS`, `DASH_CONTEXT`, `DASH_VAULT`, `DASH_VAULT_TTL`, `DASH_PEOPLE`, `DASH_DAYLINE`, `DASH_DATAFLOW`, `DASH_GROWTH`, `DASH_GROWTH_BIN`, `DASH_HB_GATE_LOG`, `DASH_PAGE_DIR`, `DASH_ASSIST_TTL` | `dash-data` | input paths and cache lifetimes for the dashboard data |
| `DASH_GROWTH_TREE`, `DASH_GROWTH_BRAIN`, `DASH_GROWTH_VAULT`, `DASH_GROWTH_DASHDATA`, `DASH_GROWTH_OUT` | `dash-growth` | |
| `DASH_DATA_TIMEOUT`, `DASH_PUBLISH_TIMEOUT` | `dash-publish` | |
| `DAYLINE_WORKSPACE`, `DAYLINE_AW_DIR`, `DAYLINE_OUT` | `dayline` | |
| `FLOWD_PORT`, `FLOWD_VM`, `FLOWD_OFF`, `FLOWD_STATE`, `FLOWD_INBOX`, `FLOWD_PROJECTS`, `FLOWD_DASHDATA`, `FLOWD_SECRETSCAN_DIR` | `flowd` | SSE port, web server for the reverse tunnel, kill switch, dirs, inputs |
| `PROXY_HOST`, `PROXY_MIRROR` | `proxy` | ssh target of the reverse-proxy host and the local mirror of its config |
