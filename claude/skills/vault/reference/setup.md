# Vault setup: Telegram login, owner console, Gmail accounts, the vault bot
Read for a Telegram (re-)login, anything the owner types at the Unraid console (`vault-console`), adding or fixing a
Gmail account, or the vault's own bot. The rules
(credentials and passwords only at the console, never via chat) live in ~/.claude/skills/vault/SKILL.md and win
over this file on any conflict.

## Telegram login (one time; the vault is logged in, so only for a re-login)
1. Owner: https://my.telegram.org → API development tools → create an app (any name).
2. API credentials: he types them himself in the Unraid console, never via chat (a token posted in
   chat would pass through me): Docker tab → claude-vault → Console → `vault-console tg-credentials`
   (api_id echoed, api_hash hidden; asks y/N before replacing; refuses while a session exists; prints only
   "telegram credentials: stored"). Check with `vault status` (credentials set). Never ask him to send
   api_id/api_hash on Telegram. Only if he already pasted them anyway: `printf '%s\n%s\n' "$API_ID" "$API_HASH" | vault tg
   set-credentials` (stdin; stored only in the vault), then delete his message holding the hash from the bot chat.
3. Me: `vault tg login-qr --timeout 180` with `run_in_background`; on every `QR_READY` / `QR_REFRESHED
   path=~/.cache/vault/login-qr.png` send that PNG as a photo (tokens last ~30 s; the file is rewritten and deleted
   at the end). He needs a second screen: he opens the photo on his PC (Telegram Desktop/Web) or a tablet and scans it
   with the phone app (Settings → Devices → Link Desktop Device). The login URL is never printed.
4. `PASSWORD_NEEDED` → the command exits 10 and the vault waits (until ~10 min after the login start). Best: he types
   the 2FA password himself on Unraid so it never passes through me: Docker tab → claude-vault → Console →
   `vault-console 2fa` (or in a terminal `docker exec -it claude-vault vault-console 2fa`). If he sends it to me
   instead: `printf '%s\n' 'PASSWORD' | vault tg 2fa` (single quotes; straight to tgread's stdin in the vault, never
   stored) and delete his password message in the bot chat. Then `vault tg login-status`. `PASSWORD_INVALID` → again.
5. `LOGIN_OK` → he gets a Telegram "new login" notice (expected). The vault then syncs the last 7 days at once and
   runs the live listener (`tgread live`: one receive-only connection, stores new messages as they arrive, syncs
   every 15 min itself, resumable backfill, queues new INCOMING messages for the event feed; own messages, the vault's
   bot chats (current + every earlier one, i.e. my chat bot too) and Telegram's login-code chat 777000 are never
   queued; the latter two are never stored either). Commands needing the connection (sync, download, status --check,
   exclude add/rm, wipe) pause it briefly. No cron needed here. Problems arrive as ~/inbox/vault-alert-*.md (cron
   `vault alerts`).

## Owner console (vault-console)
`vault-console tg-credentials`, `vault-console 2fa`, `vault-console secret NAME` and `vault-console pending |
approve RID | deny RID` (y/N confirm; works in both approval modes) run INSIDE the vault (Unraid console/terminal, the
owner only): tokens, hashes and passwords are typed without echo, and nothing typed reaches me.
`secret` prompts in plain words (telegram: "Token of a NEW bot from @BotFather used ONLY by the vault …" hidden, "Your Telegram chat id …";
paperless/firefly: address, then API/personal access token hidden). A console secret may replace a stored one without a code (he's
present). Use it for anything I shouldn't know, e.g. the dedicated vault bot (turns the approval buttons on): he
creates a NEW bot with @BotFather (/newbot), sends it /start, then `vault-console secret telegram` (new bot token + his
chat id); the vault notices the new bot id and polls it (callback queries only); my chat bot stays
send-never-poll and its chat stays excluded. A 409 Conflict (someone else polls that bot) marks it shared for good
(alert; code fallback). It's also the only fix when the bot token changed (BotFather revoke):
`vault secret rotate` would need a code sent with the dead token. Console access works only from inside the
container (127.0.0.1 plus a per-start key in /data/state) and only for Telegram credentials, 2FA and secrets.

## Gmail accounts (read-only)
Scope: Gmail in the vault, including a poll that tells me when new mail comes in.
- Setup (the owner, once per account): Google account → Security → 2-Step Verification (on) → App passwords
  (myaccount.google.com/apppasswords), name it "claude-vault", then at the Unraid terminal
  `docker exec -it claude-vault vault-console secret gmail [LABEL]` (asks the label if not given, Enter = main;
  then the address, then the 16 letters hidden; spaces fine; y/N before replacing an existing label). It logs in at
  once and answers "Gmail IMAP login OK[ (account L)], INBOX opened read-only (N mails)". I never see the password.
- Several accounts: secret `gmail` = label **main** (the
  first); each further account = secret `gmail:LABEL` (`vault-console secret gmail work`), max 5. Each has
  its own IDLE + poll, positions, folder config, auth block and alerts (vault-gmail-auth-LABEL). REFs carry the
  account: `main:1234`, `work:1234@Folder` (a bare `1234` only while there is one account; else `--account L`).
  `vault gmail search` covers all accounts unless `--account L` (results merged newest first; an account that fails
  is listed under errors); get / to-paperless / config / `raw request --source gmail` take `--account L`. Results,
  events (`account` field) and status show the LABEL and the domain only, never the address. Replacing an account's
  secret from my side: `vault secret rotate gmail:work --reason …` (his code), but normally he just reruns the console.
