# Contributing

Thanks for your interest. This repository is one person's assistant setup published as a reference, so it
follows that setup's needs. Bug fixes, portability improvements, tests and documentation are the most likely to
be merged; large features or changes that only fit a different stack may be declined. Open an issue first if
you plan something bigger.

Security problems: do not open an issue or pull request. See [SECURITY.md](SECURITY.md).

## Pull requests

- **Small and focused.** One change per pull request, with a short description of what and why.
- **Run the tests.** `tools/run-tests.sh` runs the offline suites (`tools/run-tests.sh <suite>` for one). If you
  touched a hook, also run `python3 -I claude/hooks/test_<hook>.py`; for the plugins, `bun test` in
  `plugins/telegram-plus` and `node --test plugins/heartbeat/test/`.
- **Add or update tests** in `tests/<tool>/` for behaviour you change. Tests must stay offline: use the existing
  shims and fixtures (`<TOOL>_BIN`, fake clocks, temp state and inbox dirs, a stub `tg`).
- **Follow the existing style.** Python scripts use the standard library where possible and run as
  `python3 -I`; every tool has `--help`; state goes under `~/.local/state/<tool>/`; jobs stay silent when
  everything is fine; credentials are read from `~/.secrets/` and never printed.
- **Keep docs in step.** New script: add it to `templates/workspace/TOOLS.md` and `docs/CONFIGURATION.md` (its env vars and
  config files). New cron job: `crontab` and `templates/workspace/JOBS.md`.

## Never commit personal data or secrets

- No tokens, passwords, keys, `.env` files, `access.json`, memory files or transcripts.
- No real names, addresses, phone numbers, e-mail addresses, Telegram ids, hostnames or IP addresses. Use the
  placeholders the repo already uses: `homesrv`, `example.net`, `198.51.100.x` / `203.0.113.x`, `Exampletown`,
  `owner@example.com`, `100000001`.
- Test fixtures must be synthetic. Don't record real pages, mails or documents that contain personal data.

CI runs the tests, [gitleaks](https://github.com/gitleaks/gitleaks) and a privacy gate (PII patterns, prose
checks and a private denylist) on pushes to `main` and on pull requests. A hit blocks the merge. Pull requests from forks
don't get the denylist secret, so the maintainer re-runs the full check before merging.

## Licensing

No sign-off, DCO or CLA is required. By submitting a contribution you agree that it is licensed under the
[Apache License 2.0](LICENSE), the same license as the project ("inbound = outbound", as in section 5 of the
license).

`plugins/telegram-plus` is a fork of Anthropic's `telegram@claude-plugins-official` plugin. Keep its license
file and attribution intact when you change it.
