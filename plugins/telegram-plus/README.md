# Telegram Plus

Enhanced fork of `telegram@claude-plugins-official` 0.0.7. Same access control (pairing,
allowlists, groups, permission relay) and the same state dir `~/.claude/channels/telegram`,
so it is a drop-in replacement. Marketplace: `/home/node/plugins` (name `home-plugins`).
Skills: `/telegram-plus:access`, `/telegram-plus:configure`.

New/extended tools: `reply` (format rich|html, inline buttons + on_press, reply keyboard,
remove_keyboard, force_reply, silent, effect, link_preview, album), `edit_message` (formats,
buttons), `react` (empty removes), `draft` (sendMessageDraft streaming in DMs, edit fallback in
groups; finalized by the next reply), `chat_action`, `send_poll`, `send_location`,
`pin_message`, `delete_message`, `set_commands`.

Fixes (0.1.13). A rejected rich/markdown message re-sent as several plain parts quotes `reply_to` on the
first part only (all parts with access.json `replyToMode: "all"`). Long messages split on grapheme
boundaries, so emoji (surrogate pairs, ZWJ sequences, flags, skin tones) and accented letters are never
cut; a single grapheme longer than the limit is cut between code points. A missing or empty `chat_id`
fails with "chat_id is required" (was "chat undefined"); a numeric `chat_id` is accepted as a string.
Buttons accept `callback_data` as an alias for `data`.

Rich line breaks (0.1.12). Rich markdown follows GFM, where a single newline is a soft break, so
"🎬 …\n🛡 …" or "• a\n• b" rendered as one run-on paragraph. In every rich send (reply, edit_message,
draft streaming and its edit fallback, the button "✓ choice" edit) a single newline between two plain
text lines (or two quoted lines) gets a `<br>` (a supported rich HTML tag). Left alone: fenced code and
`$$` math blocks, tables, list items, headings, rules, HTML/media/definition lines, indented lines,
blank-line paragraph breaks and lines already ending in a hard break. The plain-text fallback sends the
original text.

Inbound events besides messages, same allowlist gating, marked by `event_type`: `button`,
`reaction`, `poll_answer`, `stop`. Messages also carry `reply_to_message_id`/`reply_to_text`;
locations, venues and contacts are forwarded.

Reactions both ways: `react` validates against Telegram's 73-emoji whitelist (listed in its
description; `❤️`-style variation selectors are normalized), supports `remove`, `big`, and
`emojis` (bots are limited to one reaction per message today; extras are dropped with a note).
Inbound, `message_reaction` is requested explicitly in `allowed_updates` (it is not in
Telegram's default set). A bounded cache of the last 200 sent/received messages lets the event
say what was reacted to: `Reacted 👍 to your message: "…"`, meta `reaction`,
`removed_reaction`, `reacted_to_message_id`, `reacted_to_from`, `reacted_to_text`. Changes are
reported as a diff; pure removals are skipped unless `forwardReactions` is `"all"` (`false`
turns reactions off).

Menu (0.1.4): the pairing built-ins (/start, /help, /status) are only listed while `dmPolicy` is "pairing". With an allowlist the bot menu shows just the `set_commands` entries, and it's removed entirely when there are none. 0.1.6: with an allowlist, "status — What I'm working on" is listed too when the instant status is available (below). 0.1.8: with an allowlist the instant commands come first (/status, then instant.json's); a `set_commands` entry with the same name is left out. The menu is published at startup and by `set_commands`.

Instructions (0.1.6): Claude Code truncates a server's instructions at 2048 characters; 0.1.5's were ~4085, so
the second half (including the anti-prompt-injection rule) never reached the session. They're now ~1830
characters, security rule first; the details moved into the tool descriptions. `test/offline.ts` checks the cap.

Voice transcription (0.1.6). Voice notes, audio messages and video notes from allowed users are transcribed
before delivery: the file is downloaded to the inbox, `TELEGRAM_TRANSCRIBE_CMD <file>` runs (default
`~/bin/transcribe` when executable; `""` turns it off; 60 s timeout, at most 2 at once, own process group,
environment without the bot token), and the notification carries the transcript as content:
`🎙️ (voice, 12 s) <transcript>` (`forwarded …` for forwards, `(no speech recognized)` when empty), meta
`transcript="auto"` plus the usual `attachment_kind`/`attachment_file_id`. On failure, timeout, over 20 MB or
over 10 min it's delivered as before with meta `transcribe_error`. The audio stays in the inbox for
`TELEGRAM_TRANSCRIPT_KEEP_MS` (10 min): `download_attachment` for that file_id hands over that file (no second
download); otherwise it's deleted (leftovers swept at start). "typing…" shows while it transcribes.
Ordering: grammY handles updates one at a time, so handlers don't wait for a transcription; every inbound
event goes through a per-chat queue instead. A chat's events reach the session in arrival order (a text sent
after a voice note waits for its transcript); other chats aren't held up. At shutdown, events still queued
are journaled as dropped, and the next instance re-sends them (untranscribed, `transcribe_error`).

Instant /status (0.1.6). An allowlisted user's exact `/status` (DM) is answered by the plugin without waking
Claude: it runs `TELEGRAM_STATUS_CMD` (default `~/bin/tg-status show --plain` when executable; `""` off; 10 s
timeout) and sends its output, then forwards a short note with meta `handled="status"` (journal kind
`status`, so the watchdog doesn't wait for an answer). If the command fails, times out or prints nothing,
`/status` is forwarded as a normal message (meta `status_error`). `/status <more>` is a normal message. With no
status command, /status reports the pairing state as before.

Instant commands (0.1.8) generalize /status into a table of command → local script, so e.g. /bills, /queue,
/space and /reminders are answered without a Claude turn. The table is /status (above) plus
`instant.json` in the state dir (`TELEGRAM_INSTANT_FILE`; `""` off), a JSON array of
`{"command", "description", "run", "timeout_ms"?, "on_error"?, "note"?, "args"?}`. `run` is a command line (split on
whitespace, no shell, leading `~/` expanded) or an argv array; `timeout_ms` defaults to
`TELEGRAM_INSTANT_TIMEOUT_MS` (20000). Bad entries and the built-in names (start, help, status) are skipped
and logged. The file is re-read when it changes, so edits work without a restart (call `set_commands` to
refresh the menu). An allowlisted user's exact `/name` or `/name@thisbot` in a DM runs the script (own
process group, no bot token) and its stdout is sent as plain text; the session gets a note with meta
`handled="<name>"` and the answer repeated (≤1500 chars), journal kind `instant`. `"note": false` skips that
note (no Claude turn at all). Failure, timeout or no output with `on_error` "reply" (default): the owner
gets the script's stdout if it printed a fallback (non-zero exit), else `⚠️ /name didn't work (…)`, and the
session is told (meta `instant_error`, even with note false). `on_error` "forward": the command goes to the
session as a normal message (meta `instant_error`), as /status does. `"args": true` (0.1.10): `/name <text>`
is instant too; the trimmed text (≤512 chars, else a normal message) is passed to the script as ONE extra
argument (no shell), a bare `/name` runs it without one, and the session note starts with the whole command
(e.g. `/name <text>`; no table entry uses args since /recall was retired 2026-10-13). Otherwise `/name <args>`, and always unknown commands, groups, other users and
`/name@otherbot`, stay normal messages. The owner's table comes from `~/bin/tg-cmd`:
`tg-cmd --instant-json > ~/.claude/channels/telegram/instant.json`.

Private scans (0.1.11). A photo or document from the owner (allowlisted; in a DM, or a group the plugin delivers from) whose caption starts with
`/scan private` or `/sp`, optionally followed by a title, never reaches the session: the plugin downloads it
into a fresh 0700 temp dir under TMPDIR (file 0600), runs `paperless upload FILE --title=<title or "Scan
YYYY-MM-DD HH:MM"> --tag Inbox --tag private --create` (`TELEGRAM_SCAN_PRIVATE_CMD`, default `paperless` on
PATH, else `~/bin/paperless`; `""` off; one upload at a time, `TELEGRAM_SCAN_PRIVATE_TIMEOUT_MS` 300000),
deletes the dir whatever happens (also at shutdown; a dead instance's dirs are swept at start) and replies
`🔒 Filed privately → document <id>` or `⚠️ Private scan failed: <short reason>`. The session gets one line
with the command only, meta `handled="scan-private"` + `doc_id` (or a `scan_error` code), journal kind
`instant`; no image_path, file id or title. Albums: the caption sits on the first item and the rest of that
media group follows it, each photo its own document (`<title> (2)`, …); a caption on a later item only covers
the items from there on (earlier ones have already gone to the session). Videos, audio and voice notes with such a caption are refused, not forwarded; a
plain `/scan` caption is a normal message, as before.

Photo or document (0.1.9). `reply` `files` images normally go as photos, but Telegram's sendPhoto refuses
images over 10 MB, with width + height over 10000 or with an aspect ratio over 20 (a 1905x8710 page
screenshot failed with PHOTO_INVALID_DIMENSIONS). Images it does accept are downscaled to about 2560 px on
the long side, which makes a very tall or wide one unreadable (a 1080x7000 phone screenshot arrives as
~400x2560), so images with an aspect ratio over 4 (`PHOTO_READABLE_RATIO` in photo.ts) go as documents too;
1080x2400 phone screenshots (2.2) and ordinary 4:3 / 16:9 photos stay photos. `photo.ts` reads the size and
the dimensions from the file header (PNG, JPEG, GIF, WebP; a few small reads, no image library) and sends
such images with sendDocument, and an album containing one as an album of documents. If Telegram still
refuses a photo (400/413 with a PHOTO_* / IMAGE_PROCESS_FAILED / too-big error), it is re-sent once as a
document (an album: once as a document album); other errors (blocked, rate limit, network) are not retried.
Either way the tool result carries a `note:` saying which file went as a file and why. Unit tests: `bun test`
(test/photo.test.ts); end to end in `test/offline.ts`.

Typing (0.1.5) is state-based. Inbound messages and button presses from allowed users are pending
owner events (reactions, poll answers and Stop presses aren't). An event stops being pending when you
answer in that chat after it (`reply`, `draft`, `send_poll`, `send_location`; one answer clears every
earlier event; `react`, `edit_message`, `pin_message`, `delete_message` don't), when the tg-status board
(`~/bin/tg-status`, its state.json) records a turn end after it (`idle_at` > event + 1 s), or after
5 minutes. "typing…" shows while an event is pending AND the board says `working`, or the newest
event is younger than 12 s (pickup grace). So a message nobody picks up stops after ~12 s, and a turn
end stops it at once: the rule runs on every 4.5 s refresh and as soon as state.json changes (fs.watch
on its directory, rename-safe). Missing or unreadable state.json: the old rule (until an answer, at
most 90 s). Race guard: no chat action fires while a send to that chat is in flight or within 1.5 s
after it (unless something newer arrived), and every send first waits for an in-flight chat action,
so "typing…" never lands after the answer. An explicit `chat_action` lasts until the next answer,
`stop: true`, a turn end, or 2 minutes. Telegram drops the indicator ~5 s after the last refresh.

Delivery guard (0.1.5), "no silent loss". Claude Code's MCP handshake doesn't tell a channel server
whether its `notifications/claude/channel` will be delivered: client capabilities carry no channel
bit, and a dropped notification produces no error. Claude Code decides at connect (its channel gate:
the `tengu_harbor` feature flag, the `allow_channels` org policy, `channelsEnabled` /
`allowedChannelPlugins`, the session's `--channels` list, the protocol era) and again per
notification (flag + policy), and logs both to its MCP debug log for this server
(`~/.cache/claude-cli-nodejs/<cwd>/mcp-logs-plugin-telegram-plus-telegram/<start>.jsonl`): "Channel
notifications registered" or "Channel notifications skipped: <reason>", and one
"notifications/claude/channel: <first 80 chars>" line per accepted notification. The server sends a
`roots/list` request, which Claude Code logs as "Received ListRoots request from server". That line
identifies the server's own log file, and it serves as an ordered marker: a notification sent at
least 1 s before a marker, with no line of its own before it, was dropped.
- Polling starts once the verdict is in. If it's skipped, the server doesn't poll, so Telegram keeps the
  updates (24 h) until a session can take them. With no log or no verdict within 15 s the verdict is
  "unknown" and the server polls as before.
- Every inbound event is journaled (`inbound-journal.jsonl`, 0600, last 100 events / 200 KB) and checked
  against the log. A drop pauses polling (`bot.stop()` confirms only what was handled). The oldest
  dropped event is re-sent every 60 s as a probe; once one gets through, the rest follow (meta
  `redelivered="true"`, text prefixed "(re-sent after a connection problem…)") and polling resumes. Drops
  are acted on only after this process has seen an accepting gate line and at least one ack line.
  Otherwise they are just marked `unconfirmed`, so a changed log format can't cause duplicate re-sends.
- The owner gets one "⚠️ (auto)" message per incident (`delivery-alert.json` marks it open; silent
  00–08 local, `TELEGRAM_QUIET_HOURS`) and one "✅ (auto) Back to normal" when it ends.
- `health.json` (0600, written by the bot.pid holder): version, pid, poll role (`poll|blocked|…`),
  `last_poll_ok` (last getUpdates round-trip, writes throttled to ~20 s), `last_inbound` (owner
  messages/buttons), `last_tool_call`, `last_answer`, `channels_accepted`, `delivery`, `mcp_log`.
- `replay-request.json` (`{"ids": [journal ids]}`, written by the watchdog): events the session never
  saw. Each is re-sent flagged redelivered, at most twice.
Keep `@modelcontextprotocol/sdk` on ^1.x: Claude Code doesn't register channel servers built on SDK 2.x.

Watchdog: `ops/channel-check` (installed as `~/bin/channel-check`, cron every minute) reads health.json,
the journal, bot.pid, Claude Code's MCP log, the session transcript and `claude agents --json` (only
when needed). It tells the owner once per incident and restarts the session when inbound is dead and
the session is idle or hung (rate-limited). Details: `channel-check --help`. Its tests:
`python3 -I test/channel-check.py`.

Who polls (Telegram allows one getUpdates consumer per token): only an instance whose Claude
Code process lists telegram-plus in `--channels` or `--dangerously-load-development-channels`
is eligible; others (agents/workers that merely have the plugin enabled) never poll, but their
tools work. Among eligible instances the newest wins, as upstream — that is what carries
polling over when the main session moves into a new (e.g. daemon-hosted, forked) process. A
previous telegram-plus poller is not killed: it sees the newer `bot.pid` (or a 409) and stands
by, and takes over again if the holder dies or is orphaned. A non-telegram-plus holder (the
official plugin) or an orphan is SIGTERMed as upstream. `bot.pid` is `<pid>\ntelegram-plus`,
which upstream's `parseInt` still reads. `TELEGRAM_FORCE_POLL=1` makes any instance eligible.

Env: `TELEGRAM_API_ROOT` (mock Bot API for tests), `TELEGRAM_NO_POLL=1` (tools only; never polls
or touches bot.pid), `TELEGRAM_FORCE_POLL=1`, `TELEGRAM_POLL_CHECK_MS` (ownership check
interval, default 5000), `TELEGRAM_STATUS_STATE` (the board's state.json, default
`~/.local/state/tg-status/state.json`), `TELEGRAM_TYPING_MAX_MS` (default 300000),
`TELEGRAM_TYPING_NO_BOARD_MS` (cap without a readable board, default 90000),
`TELEGRAM_TYPING_REFRESH_MS` (default 4500), `TELEGRAM_TYPING_GRACE_MS` (default 12000),
`TELEGRAM_TYPING_TRACE=1` (typing decisions on stderr), `TELEGRAM_MCP_LOG_DIR` (Claude Code's MCP log dir
for this server; default: searched under `~/.cache/claude-cli-nodejs/*/`), `TELEGRAM_VERDICT_WAIT_MS`
(15000), `TELEGRAM_ACK_DELAY_MS` (1500), `TELEGRAM_MARKER_WAIT_MS` (6000), `TELEGRAM_REDELIVER_MS` (60000),
`TELEGRAM_GATE_RECHECK_MS` (30000), `TELEGRAM_HEALTH_POLL_MS` (20000), `TELEGRAM_REPLAY_CHECK_MS` (5000),
`TELEGRAM_QUIET_HOURS` ("0-8"; "" off), `TELEGRAM_TRANSCRIBE_CMD` / `TELEGRAM_STATUS_CMD` (split on
whitespace, no shell, leading `~/` expanded; "" off), `TELEGRAM_TRANSCRIBE_TIMEOUT_MS` (60000),
`TELEGRAM_STATUS_TIMEOUT_MS` (10000), `TELEGRAM_TRANSCRIPT_KEEP_MS` (600000), `TELEGRAM_INSTANT_FILE`
(default `<state dir>/instant.json`; "" off), `TELEGRAM_INSTANT_TIMEOUT_MS` (20000). Tests: `bun run test` = `bun test` (unit) + `bun test/offline.ts` (mock Bot API with
Telegram's offset semantics, plus a fake Claude Code that writes MCP log lines and answers roots/list).

---

# Telegram

Connect a Telegram bot to your Claude Code with an MCP server.

The MCP server logs into Telegram as a bot and provides tools to Claude to reply, react, or edit messages. When you message the bot, the server forwards the message to your Claude Code session.

## Prerequisites

- [Bun](https://bun.sh) — the MCP server runs on Bun. Install with `curl -fsSL https://bun.sh/install | bash`.

## Quick Setup
> Default pairing flow for a single-user DM bot. See [ACCESS.md](./ACCESS.md) for groups and multi-user setups.

**1. Create a bot with BotFather.**

Open a chat with [@BotFather](https://t.me/BotFather) on Telegram and send `/newbot`. BotFather asks for two things:

- **Name** — the display name shown in chat headers (anything, can contain spaces)
- **Username** — a unique handle ending in `bot` (e.g. `my_assistant_bot`). This becomes your bot's link: `t.me/my_assistant_bot`.

BotFather replies with a token that looks like `123456789:AAHfiqksKZ8...` — that's the whole token, copy it including the leading number and colon.

**2. Install the plugin.**

These are Claude Code commands — run `claude` to start a session first.

Install the plugin:
```
/plugin install telegram@claude-plugins-official
```

**3. Give the server the token.**

```
/telegram-plus:configure 123456789:AAHfiqksKZ8...
```

Writes `TELEGRAM_BOT_TOKEN=...` to `~/.claude/channels/telegram/.env`. You can also write that file by hand, or set the variable in your shell environment — shell takes precedence.

> To run multiple bots on one machine (different tokens, separate allowlists), point `TELEGRAM_STATE_DIR` at a different directory per instance.

**4. Relaunch with the channel flag.**

The server won't connect without this — exit your session and start a new one:

```sh
claude --channels plugin:telegram@claude-plugins-official
```

**5. Pair.**

With Claude Code running from the previous step, DM your bot on Telegram — it replies with a 6-character pairing code. If the bot doesn't respond, make sure your session is running with `--channels`. In your Claude Code session:

```
/telegram-plus:access pair <code>
```

Your next DM reaches the assistant.

> Unlike Discord, there's no server invite step — Telegram bots accept DMs immediately. Pairing handles the user-ID lookup so you never touch numeric IDs.

**6. Lock it down.**

Pairing is for capturing IDs. Once you're in, switch to `allowlist` so strangers don't get pairing-code replies. Ask Claude to do it, or `/telegram-plus:access policy allowlist` directly.

## Access control

See **[ACCESS.md](./ACCESS.md)** for DM policies, groups, mention detection, delivery config, skill commands, and the `access.json` schema.

Quick reference: IDs are **numeric user IDs** (get yours from [@userinfobot](https://t.me/userinfobot)). Default policy is `pairing`. `ackReaction` only accepts Telegram's fixed emoji whitelist.

## Tools exposed to the assistant

| Tool | Purpose |
| --- | --- |
| `reply` | Send to a chat. Takes `chat_id` + `text`, optionally `reply_to` (message ID) for native threading and `files` (absolute paths) for attachments. Images (`.jpg`/`.png`/`.gif`/`.webp`) send as photos with inline preview, except ones Telegram can't take as photos (over 10MB, width + height over 10000) or would shrink unreadably (aspect ratio over 4, e.g. long phone screenshots), which send as documents; other types send as documents. Max 50MB each. Auto-chunks text; files send as separate messages after the text. Returns the sent message ID(s). |
| `react` | Add an emoji reaction to a message by ID. **Only Telegram's fixed whitelist** is accepted (👍 👎 ❤ 🔥 👀 etc). |
| `edit_message` | Edit a message the bot previously sent. Useful for "working…" → result progress updates. Only works on the bot's own messages. |

Inbound messages trigger a typing indicator automatically — Telegram shows
"botname is typing…" while the assistant works on a response.

## Photos

Inbound photos are downloaded to `~/.claude/channels/telegram/inbox/` and the
local path is included in the `<channel>` notification so the assistant can
`Read` it. Telegram compresses photos — if you need the original file, send it
as a document instead (long-press → Send as File).

## No history or search

Telegram's Bot API exposes **neither** message history nor search. The bot
only sees messages as they arrive — no `fetch_messages` tool exists. If the
assistant needs earlier context, it will ask you to paste or summarize.

This also means there's no `download_attachment` tool for historical messages
— photos are downloaded eagerly on arrival since there's no way to fetch them
later.
