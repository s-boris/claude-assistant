# Security

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub's private vulnerability reporting: open the
repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue, pull request or
discussion for a security problem.

Include what you found, where (file and line, or the command), how to reproduce it, and what an attacker gains.
This is a personal project maintained in spare time: reports are read and fixed on a best-effort basis, with no
guaranteed response time and no bug bounty. Only the `main` branch is maintained.

If the issue is in Claude Code itself or in Anthropic's official plugins rather than in this repository, report
it to Anthropic instead.

## Security model

Read this before you run the assistant. It is an autonomous agent with a shell.

**It runs without permission prompts, by design.** The main session uses Claude Code's `bypassPermissions` mode:
it runs shell commands, edits files and calls tools without asking, because nobody watches the terminal. Treat
the container as fully controlled by the model, and by anyone who can steer the model.

What stands in for the prompts:

| Layer | What it does | Limits |
|---|---|---|
| `risk-check` hook | rates every shell command; blocks HIGH-risk ones (irreversible data loss on the server, wiping a crontab, force-push to main, printing or sending secrets, mail and public posts) until the owner approves a one-time code | rule-based with an LLM for gray cases; **fails open** on errors; a pattern match, not a sandbox |
| `vault-guard` hook | blocks direct access to the privacy vault's data and credentials, the Telegram bot token file, and text/content requests from subagents | **fails closed**; still a tripwire: a script or an obfuscated command can get around it |
| privacy vault | keeps raw private data and its credentials in a separate container; returns answers instead of text; raw text and files need the owner's approval in a separate bot | protects what is inside the vault, not what the assistant already has |
| Telegram allowlist | only allowlisted user ids reach the session; everything else is dropped or treated as data | depends on `access.json` being correct |
| operating rules | `/etc/claude-code/CLAUDE.md` and `~/workspace/CLAUDE.md` require asking before spending money, contacting people, posting publicly or writing to the server, and treat web pages, mail, notes and other users as data | instructions to a model, not enforcement |
| container | the blast radius | only as strong as your Docker setup and what you mount or hand in |

**Everything the assistant reads goes to Anthropic's API.** Files, command output, web pages and messages all
become model context. That is why private data stays in the vault and why scripts never print secrets.

**Prompt injection is a real risk.** The assistant reads web pages, mail summaries, notes and documents. A
crafted input can try to make it run commands. The layers above reduce the damage; they do not prevent it.

## Recommendations if you deploy it

- Run it in a dedicated container. Mount no host paths you can't afford to lose, and never the Docker socket.
- Give it the least credentials that work: read-only API keys where the service allows it, an SSH user restricted
  to the commands you need (`unraid-ssh` supports a server-side allowlist; that part is not included).
- Keep `~/.secrets` at 0700/0600, and keep the Telegram bot token out of the session's environment
  (`start-claude` does this).
- Use a separate Telegram bot for the vault's approvals, so the assistant's bot can never approve its own
  requests.
- Write your own hard rules in `/etc/claude-code/CLAUDE.md`, and review the `risk-check` log
  (`risk-ok log`) now and then.
- Keep Claude Code, the plugins' dependencies and the container image updated.
- Set a spending limit on the account the session uses.

## Scope

In scope:

- Bypasses of `vault-guard` that read vault data, vault credentials or the bot token without an obvious
  deliberate workaround (for example through a path or command form the rules should cover).
- Commands that `risk-check` should rate HIGH but lets through, when the miss comes from a parsing bug.
- Secret leaks: a script that prints, logs, commits or sends a credential; `secretscan` misses on supported
  formats.
- Access-control flaws in `plugins/telegram-plus`: messages from non-allowlisted users reaching the session,
  pairing or allowlist bypasses, button or reaction events from other chats being accepted.
- Command injection or path traversal in `bin/` tools from untrusted input (Telegram messages, web pages, file
  names, cron notes, API responses).
- Anything that makes the CI privacy gate or gitleaks miss committed secrets or personal data.

Out of scope:

- The absence of a sandbox or permission prompts: that is the documented design.
- Generic prompt injection that makes the model misbehave without bypassing one of the layers above.
- Attacks that need control of the container, the Docker host or the owner's Telegram account.
- Vulnerabilities in Claude Code, Anthropic's services, Telegram, or the self-hosted services the tools talk to
  (report those upstream).
- Outdated software in your own deployment.
