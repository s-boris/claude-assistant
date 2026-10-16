# Vault internals: how it works
Read when explaining or debugging how the vault works: the policy pipeline, the anonymization cache, the LLM queue
and metrics, async jobs and speed, the owner's live view, the vault-bot status board, audit and alerts. The rules
live in ~/.claude/skills/vault/SKILL.md and win over this file on any conflict.

## Policy layer
Everything I read goes to Anthropic's API, so the owner's raw private data lives in a SEPARATE container that I can't
read: `claude-vault` on Unraid, reachable only from the Docker network src_default at http://claude-vault:8300 (no
LAN port). I call its API with `vault ...` (token ~/.secrets/vault-token; the vault stores only its sha256). Every
answer passes the policy layer: regex net -> local LLM anonymizer (llm-local policy `llm-local-v4.1+regex-2026-10-13`:
names, addresses, non-financial facts kept; intimate passages and anyone's money/business details, the owner's own
included -> `[private: …]`, money figures -> `[amount]`; identifiers masked) -> regex net. Same policy for Telegram
(search/recent/summaries), Gmail (search/get) and the event feed. Paperless = fields: `paperless get` is a card (no
content); tier-1 `paperless fields` are free; document content (anonymized, `--keep-own-money`) and tier-2 fields
(identifiers, addresses, dates) only after his ✅ per document. No fallback: if the LLM fails, I get an error, never
raw text.
- Money: Swiss money words and amounts in words are in the net; investment/debt sentences with a personal sign ->
  `[private: money]` in code. Gmail output (search, get, events, attachment names) masks EVERY currency amount and
  every number after an amount label (`--all-amounts`); chats mask amounts near payment words (paid, bezahlt, TWINT,
  Total:, Betrag…).
- Paraphrase (only behind `--text` + his ✅, see "Answers, not text" in SKILL.md): Telegram (search/recent/unread/
  summaries) and Gmail (search subject+snippet, get) text arrives as checked paraphrases of the anonymized text IN
  THEIR OWN LANGUAGE (no translation; Swiss German stays Swiss German; an item paraphrased into the other language
  is retried), without changing any meaning, number or fact; one local call per batch; numbers/dates must occur in
  the anonymized input (else retry once, then the anonymized line marked `[not paraphrased]`, never raw). Not
  verbatim: don't quote it as their words. Paperless and media facts are not paraphrased.
- Anonymization cache: repeating the same tg/gmail read is near-instant (cached in the vault, private jobs only,
  never `vault llm-run`); a re-read after one new message re-sends only ~2 chunks; `tg purge/exclude add/wipe` clear
  it; a tg read whose caller gave up still finishes, so just retry.
- `vault metrics [--days N]`: hourly numbers (calls, errors, p50/p95 per route, LLM busy share and waits, cache
  hits; a line in `vault status`). The LLM queue runs live > bulk > background and drops work whose caller has gone
  (`vault status` "LLM queue:", the client prints a progress line after 10 s; VAULT_QUIET=1 off).
- Per-request gating (app/llmproxy.py): every model request takes the queue on its own, so a live read waits for at
  most one bulk request (a bulk request that holds it up 20 s is aborted and re-run after it), not a whole summary
  part; `vault status` shows `gating: per request`. Interactive `gmail get`/`tg search` during a summary job: ~1 min.
- The owner can watch my calls live in the vault bot: `/watch on [full] [30m|2h]`, `/watch off`. One plain line per
  call ("00:05:12 🔎 ask tg · 'Wochenende' · yesno → ✅ answer: no 2026-10-10 (past) … · 14 s"; no JSON); LLM busy /
  job progress is one line edited in place. Full adds Original + Claude got as "key: value" quotes; RAW goes to him
  only, memory only, never to me; `vault status --json` → watch.

## Async jobs and speed
| Command | Does |
|---|---|
| `vault --async tg search\|recent\|unread-summary-data\|summarize …`, `… gmail search\|get …`, `… paperless get …`, `… media facts …`, `… facts …`, `… find …`, `… llm-run …` (`--async` anywhere before `--`) | runs it as a vault job, prints the id at once. **Rule: anything that can take over ~2 min uses `--async` + `vault job wait ID --max 230`** (exit 0 = done, printed like the command with its exit code; **12 = still running**: wait again; never one call over 4 min). Subagents loop `job wait`; my own session: Monitor until-loop or `run_in_background`. `tg summarize` as a job: per chat (busy chats per day, small chats together) at bulk priority, then ONE merge (llm-local --summary --paraphrase on the part summaries); `job wait --partial` shows the parts so far |
| `vault job wait ID [--max 230] [--partial] [--json] \| list \| cancel ID` | jobs: results kept 30 min in vault memory (20 MB cap), only for my token; identical requests join the running job; a vault restart drops all jobs (submit again: parts are cached). Never raw/secret/console/write routes. /watch shows each job's submit and, once, its result |

Speed:
- The anonymizer needs ~30 s per call plus ~20–60 s per 1000 characters; a summary adds ~2 min. A 30-message chat
  takes ~1–2 min; a busy day of unread messages 15+ min. The vault runs one LLM job at a time (others wait).
- **Anything over ~2 min runs as a vault job (rule):** add `--async` (it prints a job id at
  once), then `vault job wait ID --max 230` (exit 0 = done, printed like the command; **exit 12 = still running**:
  call it again). Never one blocking call over 4 min, never `timeout 280 tgread …`. A subagent loops `job wait`; in my
  own session use a Monitor until-loop on it, or `run_in_background`, and keep the progress-line rule.
  `tgread summarize --async` goes per chat (busy chats per day) at bulk priority, then one merge: `vault job wait ID
  --partial` shows the part summaries done so far; `vault job list` / `vault job cancel ID`. Identical requests join
  the running job; results are kept 30 min; a vault restart drops jobs (submit again: the parts are cached).
- Input above 40000 characters is refused with exit 2 and advice: narrow it (`--chat`, `--hours`, `-n`,
  `--per-chat`, `--since`), or pass `--llm-max-chars N` in the background if he really wants it all.

## Status, audit, alerts
| Command | Does |
|---|---|
| `vault status` | vault, LLM, Telegram login/sync + live listener, sources, pending raw requests + approval mode (buttons / fallback code), event feed, alerts (no data) |
| `vault audit [--days 7]` | what was asked, when (metadata only, never content/codes) |
| `vault alerts [--to-inbox]` | sync/LLM problems from the vault (cron copies them to ~/inbox every 15 min) |

## Live view for the owner
He can watch live what flows between me and the vault. (1) One stderr line per API call: Unraid → Docker →
claude-vault icon → Logs (or `docker logs -f claude-vault`). It shows the caller, method and path, the parameters I
send (only allowlisted keys like query/chat/since/id; secret routes show `key=(hidden)`; `text` only as a size), the
status (`(exit N)` when the tool failed), the size, the ms, and for filtered results `redacted: N private (…), M
masked (…)`; raw releases are marked RAW. Never a response body. (2) `vault-console watch [--full]` (Docker →
claude-vault → Console, or `docker exec -it claude-vault vault-console watch --full`) prints the last 10 calls,
then follows live from an in-memory ring buffer (5 MB, gone at restart). `--full` adds each response body as I got
it, cut to 4 KB, with secret values and live codes scrubbed. Ctrl-C stops it. It's console-only (GET /console/events,
console key; my token gets 404), and vault-guard blocks me from running it. `vault admin logs` shows me the
same lines, nothing more.

## Pinned status in the vault bot chat
app/board.py: one silent, protected message the vault sends to his vault-bot chat, pins (disable_notification) and
edits in place: at most every 30 s on a change, every 10 min for the clock, at once when he sends `/status` to the
vault bot. Line 1 = health dot + activity (Idle / waiting for your ✅ / sorting N new msgs / anonymizing / Firefly
import / syncing Telegram / checking Gmail); Today: Claude's requests by kind (TG/mail/docs/money/LLM/raw; status,
event polls and release polls not counted), raw asks ✅/❌/⌛, redactions in what left the vault, new Telegram msgs /
mails → how many surfaced to me vs chatter (event store), Firefly imports; Health: TG live, Gmail, Paperless, Firefly,
LLM, feed, open alerts, policy version, clock. Counts and states only. Deleted → new message + pin (≤ 1 per 2 min);
unpinned → re-pinned at the next check (getChat on his chat: id + date only; if he pinned something newer himself it's
left alone). Only with the vault's OWN bot polling (a shared bot would cover my own tg-status pin); off in test mode or
with env VAULT_NO_STATUS_BOARD=1. Counters: /data/state/status-board.json (reset at midnight Zurich). I see it only as
`vault status` → "pinned status (vault bot chat): pinned, last edit …" (never its text; never read that chat).
Docker log lines "status board: …" on create/re-pin/errors. Bot API methods: sendMessage, getUpdates,
answerCallbackQuery, editMessageText, getFile, pinChatMessage, getChat (owner's chat only; test-enforced).
