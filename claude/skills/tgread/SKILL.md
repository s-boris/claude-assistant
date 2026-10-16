---
name: tgread
description: Read-only archive of the owner's own Telegram chats (his account, not the bot), kept INSIDE the privacy vault (claude-vault); the vault's local model answers questions about them (answers and digests, not text; text only with his ✅). Use when the owner asks "what did X write", "what did X say about …", "search my chats for …", "find the message where …", "summarize my unread", "what did I miss on Telegram", "what's new in group Y", "did anyone write about …", or asks to exclude/purge a chat. Also for its QR login. `tgread …` = `vault tg …` (skill vault for the vault itself and the privacy rules).
---
# tgread: the owner's Telegram chats, read-only, private by default

The owner approved this: READ-ONLY access to all his Telegram chats through his own account
(MTProto, device "<server name> Reader", profile network.server_name). tgread, its session, credentials and SQLite
archive live only in the separate container claude-vault on Unraid; I can't read them. ~/bin/tgread is a thin wrapper
(`exec vault tg "$@"`): same commands and output, every answer filtered by the vault. tgread can't send, edit,
delete, forward, react, mark read, show "typing" or set him online (3-layer allowlist guard: `tgread allowlist`).
It has nothing to do with my bot chat (telegram-plus plugin).

## Rules: skill vault
The privacy rules (answers, not text; `--text --reason "<his request, quoted>"` on
search/recent/unread-summary-data/summarize only when he explicitly asks, with his ✅; never forward; ask only when he
asks; what gets masked), the credential/2FA rules and the async rule for reads over ~2 min are kept ONCE, in skill
vault ("Privacy rules", Commands). They govern every tgread command: if skill vault isn't loaded yet, load it (Skill
tool, `vault`) before the first tgread call. tgread-only additions:
- Don't pipe tgread into llm-local.
- Exact quotes / one person's exact message / a media file: only when the owner explicitly asks, and only with his OK,
  via the raw flow of skill vault: `vault raw request --source tg --query '{"cmd":"search","query":"Konzert","from":"Anna","n":5}' --reason "…"`
  (files: `tgread download --chat SEL --msg ID --reason "…"`). `--raw` itself is refused.

## Commands
| Command | Does |
|---|---|
| `tgread ask (--chat SEL \| --search Q) [--since 30d] [--kind K] ["question"] [--json]` | = `vault ask tg`: an answer, not text (kinds below; with a question and no `--kind`: `short`, at most 12 checked words). Follow-ups (2026-10-15): add `--session`, then `tgread ask --session ID "und wo?"` (= `vault ask --session …`) over the same messages, no new search; `--end` closes it |
| `tgread search "q" [--chat SEL] [--from WHO] [--since 7d] [--until D] [-n 30] [--any] [--ask "question"]` | FTS5 search (all words; `--any`, `--substring`, `--fts`), newest first: hits without text, i.e. hits only (time, chat, first name, ref); `--ask "question"` answers over them |
| `tgread recent [--chat SEL] [--from WHO] [-n 30] [--hours H \| --since D]` | the digest of those messages (flags, no text) |
| `tgread unread-summary-data [--hours 24] [--per-chat 60] [--include-muted] [--include-archived] [--include-read]` | the digest of unread messages |
| `tgread summarize [--hours 24] [--chat SEL] [--unread]` | the digest: counts, needs-you flags (who wants a reply, decision, …, due), dated plans, signal categories, topics (`--json` for the fields) |
| … `--text --reason "his request"` | the anonymized paraphrase instead, only after his ✅ in the vault bot (exit 10 first); main session only |
| `tgread chats [--type T] [--grep S] [--all]` | chat list with ids; private chats by stable alias (`--no-pseudonyms` shows real names: only if he asks) |
| `tgread exclude add SEL…` / `exclude rm SEL…` / `exclude list` | excluded chats are never fetched; adding one deletes its stored messages. My bot chat and 777000 (Telegram's login codes) are excluded by the vault itself and can't be removed |
| `tgread purge SEL --yes` | delete one chat's stored messages (fetched again unless excluded) |
| `tgread sync [--since 7d] [--chats SEL…] [--max-seconds N]` | manual pull (pauses the live listener briefly); normally the vault's live listener stores new messages as they arrive and syncs every 15 min itself |
| `tgread events [--since C\|latest] [--max N] [--json]` | = `vault events --source telegram`: new incoming messages, triaged (❗/·) + anonymized in the vault (skill vault). `~/bin/vault-events` (cron) makes ONE ~/inbox note per batch with something actionable; mute/priority per chat: `vault events mute\|priority\|normal --chat ID` |
| `tgread status [--check]` | login, archive, backfill, sync, live listener + event-feed queue, LLM, privacy settings |
| `tgread wipe --session\|--data\|--all --yes` | delete the vault's session/archive |

- Kinds: `tgread ask --chat SEL "question" [--kind yesno|when|who|deadline|actionable|requests|count|short|interests|digest]`;
  digests (flags, no text): `tgread summarize|recent|unread-summary-data`.
- Message commands also take `--sync` (quick sync first), `--full` (don't shorten long messages), `--llm-max-chars N`.
- Long reads (a busy day of unread can take 15+ min): `--async` + `vault job wait ID` (skill vault, Commands).
  Input above 40000 characters is refused with exit 2 and advice: narrow it (`--chat`, `--hours`, `-n`,
  `--per-chat`, `--since`), or pass `--llm-max-chars N` in the background if he really wants it all.
- SEL = chat id (best; from headers or `tgread chats`), `@username`, exact title or a unique part of it, `me` (Saved
  Messages), or a stable alias from `tgread chats`. WHO = a name, `@username`, user id or `me`.
- Exit codes: 0 ok, 2 usage (also "too much for the LLM"), 3 not logged in or vault unreachable (stderr says which;
  `vault admin ps`), 4 GUARD BLOCKED (tell the owner, don't retry), 6 local LLM unavailable, 10 waiting for the
  owner (his ✅ tap or code), 75 try later (FLOOD_WAIT / sync or LLM busy), 77 vault token rejected/missing.
- Its config lives in the vault; there is no API to change it (~/.claude/skills/vault/reference/admin-deploy.md).

## Chat directory (metadata only, no LLM, instant)
- `tgread find-chat "ski weekend" [--since 180d] [-n 10]`: which chat is it? Ranked by my alias/tag, title,
  names of people who posted there, matching messages, recency. Labels and counts only. Use it before `--chat`.
- `tgread alias set CHAT "dive group" [--tag tauchen]` | `alias rm NAME` | `alias list`: my names for chats; every
  `--chat` accepts them. Cleared by `purge`/`exclude add` of that chat.
- `tgread who NAME`: the person's DM chat id + the groups they posted in (counts, last date, max 20).
- Files in a chat: `vault media list tg CHAT` (= `tgread media --chat CHAT`), see the vault skill (Media).
- Chat ids and message refs are hidden from the anonymizer and restored, so they never come back as [phone].

## Answering typical requests
- "When is Anna's concert?" / "Did Anna say yes?" → `tgread ask --search "Konzert" "when is the concert?" --kind
  when` (or `--chat "Anna"`, `--kind yesno`). Answer in your own words from the answer card.
- "What did Anna write about the concert?" (he wants her words) → first the answer (`tgread ask …`); if he wants
  the wording itself: `tgread search "Konzert" --from "Anna" --text --reason "owner: what did Anna write about the
  concert"` → he approves in the vault bot → pick it up as printed. No quotes unless he asks.
- "Summarize my unread" / "what did I miss": `tgread summarize --hours 24 --unread --async` + `vault job wait ID`
  → tell him the digest (who needs him for what, plans with dates, topics), not message contents.
- "What's new in group Y?" → `tgread summarize --chat "Y" --hours 48` (digest), then `tgread ask --chat "Y" "…"`
  for a specific question.
- "Don't read chat X" / "stop archiving X" → `tgread exclude add "X"` (confirm what was excluded and that its
  stored messages are gone).

## Login (one time; the vault is logged in, so only for a re-login)
QR login, 2FA and the console steps: ~/.claude/skills/vault/reference/setup.md (the credential rules: skill vault).

## Kill switch
Owner: Telegram → Settings → Devices → terminate "<server name> Reader". Me: `tgread wipe --all --yes` (session + archive
in the vault). tgread can't log out by itself, because that is a write.

Files: canonical source ~/opt/claude-vault/vendor/tgread (deployed into the vault), tests ~/opt/tgread-tests/run.sh
(fake Telegram, stub llm-local, no network; also run in the image by every vault deploy), venv ~/opt/tgread-venv
(tests only). The old local data dir (~/.local/share/tgread) and ~/.secrets/tgread* are blocked by the vault-guard
hook.
