---
name: llm-local
description: Local LLM for private text that must not reach a cloud API (llm-local runs Gemma-4-26B-A4B inside the privacy vault; a Qwen3.5-9B llama-server on this box is a stopped rollback only). Use BEFORE private text that is already on this box enters my context: files or exports he sends (WhatsApp exports, personal documents, emails pasted to me) with intimate details, other people's personal data, phone numbers, IBANs, AHV/ID numbers. Also for "anonymize this", "remove personal data", "keep it local", "don't send it to the cloud/Anthropic", and for bulk private summarization or classification, or a plain local prompt. Not for his Telegram, Gmail or paperless: those stay in the vault (skill vault: ask, don't read). Uses ~/bin/llm-local (--anonymize, --summary) and ~/bin/llm-server-ctl.
---
# llm-local: private text is read by a local model first

Everything I read goes to Anthropic's API. CLAUDE.md "Private data stays local": private text and other people's personal data pass through the local model first, and I read only its output.

## Which tool
- His Telegram, Gmail, paperless, Firefly: the vault (skills vault, tgread, paperless). Answers, not text. Don't pipe tgread or vault output into llm-local.
- Private text already on this box (a file he sent, an export, a pasted mail): `llm-local`.
- Plain prompts that need no cloud (classification, small JSON jobs): `llm-local` too.

## The one rule that makes it work
- Pipe the raw text straight from its source into `llm-local`; read only the output.
- Never `cat`/Read the raw text first. Never paste it into a prompt, a subagent, WebFetch or a message.
- If the source command needs a file: a `mktemp -d` dir, deleted afterwards.
- Exception: he explicitly asks for an exact quote or one person's exact message in a file he gave me. For vault-held data, exact text goes only through the vault with his ✅ (skill vault).
- His own data I already hold (profile, bills, tax) is exempt.
- If llm-local fails (exit 3/4/5), do NOT fall back to reading the raw text. Retry later or tell him what failed.

## Commands
```
llm-local --summary --owner-name "<owner's name as in the text>" < chat.txt   # detailed summary of the redacted text
llm-local --anonymize --owner-name "<name>" < export.txt                      # same text, private parts + identifiers out
llm-local --anonymize --strict --owner-name "<name>" < export.txt             # also all names aliased, addresses masked
echo "<prompt>" | llm-local [--system "…"] [--max-tokens N] [--json] [--schema S] [--temp T]   # plain local prompt
```
- Prompt on stdin. Exit codes: 2 usage, 3 LLM unavailable, 4 bad response or input too long, 5 timeout.
- `llm-local --help` forwards to the vault and waits on stdin: read the flags with `LLM_LOCAL_BACKEND=local llm-local --help`.
- `--stats`: speed and counts on stderr (`private_unmatched` = a private quote the code couldn't place; it's also warned).

## Policy `llm-local-v4.1+regex-2026-10-13`
His rule: first names and addresses may pass; redact personal or intimate things that don't belong in front of a third-party AI, and his money/business details too.
- Kept: names (first names; surnames by default), addresses, places, venues, non-financial facts (topics, plans, meetings, trips, events, decisions, dates, times, who said what), small everyday shared costs, bills with due dates.
- Redacted to `[private: health | mental health | sexual | dating | relationship | family | money | business | confidential | gossip]`: intimate or personal passages about anyone. The rest of the message stays.
- `money` covers anyone's finances, his own included (salary, savings, investments, trading, debts, bank details, purchases and sums). `business`: deals, companies, clients, revenue, resale.
- Money net (regex, after the model and again on summaries): `[amount]` for any amount from 5'000 up, and for amounts near money/business or payment words (Lohn, invest, Depot, Umsatz, bezahlt, TWINT, Total:, Betrag …).
- Masked identifiers: `[amount]`, `[phone]`, `[email]`, `[IBAN]`, `[account number]`, `[card number]`, `[AHV number]`, `[ID number]`, `[secret]`, `[license plate]`, `[birthdate]`, `[link: domain]`, `@[user]`, and `[surname]` for anyone in a redacted passage.
- `--keep-own-money`: his own admin documents only (bills, payslips, tax): only debts and money troubles stay private, no `[amount]` net.
- `--all-amounts` (mail): every amount of any size. Not with `--keep-own-money` or `--json`.
- `--strict`: maximum anonymity (people → `Person A/B`, the owner → `You`, home addresses `[address]`, locations `[location]`). Use it when output may travel beyond my context, or when he asks. About 2× slower.
- `--paraphrase` (implies `--anonymize`): each message rewritten as a short neutral paraphrase in its own language (never translated; Swiss German stays). Numbers and dates are code-checked; failures fall back to the anonymized wording marked `[not paraphrased]`, never raw. Not verbatim: never quote it as their words.
- `--cache-dir` / `LLM_LOCAL_CACHE_DIR`: the vault's private jobs only, never for my own text.

## How good it is
- The model detects; code places each quote, a second per-sentence check confirms it, then regexes catch identifiers it missed.
- Strong, not a guarantee: a vaguely written private passage or an odd identifier can slip through. Treat output as private: fine for my context, never posted or forwarded, nothing in notes or memory beyond what he asked.
- Private passages have no regex backstop. Over-redaction happens (a harmless money joke, a venue named after a person).
- Summaries can slip on weekday→date arithmetic or a later change of plan: check key dates against the anonymized text when it matters.

## Speed (CPU only)
- Through the vault (Gemma): prompt ~45 tok/s, generation ~10-12 tok/s. One LLM job at a time; others queue.
- Rough guide: a 165-message chat (~12 KB) ~5 min to anonymize, ~9 min with `--summary`, ~2× with `--strict`.
- Anything bigger than a few messages: `run_in_background`, and tell him it's on its way. Context is 32k tokens; long texts are chunked (`--chunk-chars`, default 6000).

## Backend: the vault
- `~/.config/llm-local/backend` = `vault`: llm-local forwards to `vault llm-run -- ARGS` (same flags, stdin, stdout, exit codes; KV cache flushed first). Env `LLM_LOCAL_BACKEND` overrides it.
- The vault's server: llama.cpp b11669 + Gemma-4-26B-A4B IQ4_XS with an MTP drafter, sha256-checked (details and rollback: ~/reports/vault/gemma-switch-2026-10-14.md; skill vault).
- The local server here is stopped on purpose: its boot.sh and crontab lines are commented out ("moved to claude-vault").

## Local server (rollback only)
- Roll back: `rm ~/.config/llm-local/backend; llm-server-ctl start`, then uncomment the `llm-server-ctl` lines in ~/boot.sh and ~/crontab (`crontab ~/crontab`).
- `llm-server-ctl status|start|stop|restart|ensure|info|install`. 127.0.0.1:8179 only, no HTTPS, no web UI, no prompt logs. Refuses to start below 9 GB MemAvailable; ~6 GB RAM + page cache; nice 19, 8 threads.
- Pinned: llama.cpp b11541 in ~/opt/llama.cpp, Qwen3.5-9B-MTP Q4_K_M (sha256 in llm-server-ctl). Settings: optional ~/.config/llm-local/server.env (same names as its env vars; absent = defaults). Upgrade: bump `LLAMA_TAG`, delete `build/`, `llm-server-ctl install`, re-run the tests.
- It stays Qwen: a second ~19 GB Gemma server would not fit next to the vault's. ~/opt/llm-models holds the vault's Gemma seed files (the vault deploy on the server reflinks them).
- Logs: ~/.cache/llm-local/{server,install}.log.

## Tests
`~/opt/llm-local-tests/run.sh` (mocked + a real smoke test); `--real-swiss` (Swiss German paraphrase eval, ~3 min); `--real` (full anonymize/summary/strict runs on a synthetic chat with planted identifiers, ~15-20 min).
