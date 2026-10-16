# Vault admin: deploy, tests, switches, rollback, backup, guard changes
Read before `vault admin …`, preparing a deploy, flipping a switch or rolling back, the GitHub backup, or editing
the vault-guard hook. The rules live in ~/.claude/skills/vault/SKILL.md and win over this file on any conflict.

## Deploy: his, from a commit I push
My SSH key on Unraid is forced into the dispatcher (claude-bot-cmd) and runs only its read-only verbs via
~/bin/unraid-ssh (`unraid-ssh mode` = verbs): no shell, no docker exec/run/restart, nothing of the vault's folder or
volume, no writes (except storing my brain backup). I never build, start, stop or enter the vault; every deploy is the owner's.

| Command | Does |
|---|---|
| `vault admin deploy` | runs vault-git-sync (commit + push to the private GitHub repo) and prints "ready for your deploy: <commit>", the commits since his last deploy and flagged files (privacy filter, app/, Dockerfile, docker-compose.yml, vault.env, requirements/, vendor/) |
| `vault deploy request <commit> [--wait S]` | Telegram deploy: his server's runner (every minute) reviews the commit, the vault bot sends him a card with ✅ Deploy / ❌ Cancel, the runner re-checks his ✅ and deploys. `vault deploy status [ID]` until 0 = deployed, 10 = under way, 1 = not deployed (it says why). I don't message him about the card: the bot does |
| the User Scripts button `claude-vault deploy` | his: Run Script with `<commit>` (review: commits, changed files, full diffs of the privacy filter and container settings), then `<commit> confirm` (build → image tests → model files → switch → health check; on any failure the old vault keeps running). Needed when Telegram may not deploy a commit (no card; status says why: first deploy, docker-compose.yml changed, extra compose file, symlink/submodule/.gitattributes, a non-`VAULT_` line or the token hash in vault.env, odd pins) |
| `vault admin logs [-n N]` / `vault admin ps` | the read-only verbs vault-logs / vault-ps |
| `vault admin restart\|down\|init\|test\|test-instance\|env-add` | refused, his: restart/stop from the Unraid Docker tab; the API token's hash with `vault-deploy token-hash <sha256>`; the image tests run in every deploy; this server's .env values (VAULT_HA_DEVICE, VAULT_MODELS_DIR, VAULT_CPUS, VAULT_CPUSET, VAULT_MEM_LIMIT, VAULT_DEPLOY_DIR) he edits by hand (deploys keep the .env) |

- Weights: his deploy adds missing weight files pinned in app/vaultd.py (copy from ~/opt/llm-models, else a Hugging
  Face download, sha256-checked) BEFORE the switch. When a pin changes, warn him it may download GBs and that the
  review lists it as "WEIGHTS CHANGED".
- Rollback: his deploy of an earlier commit (the review calls it ROLLBACK; `vault-deploy status` lists the history).
- Design + trust trade-offs: ~/opt/unraid-lockdown/DEPLOY-VIA-TELEGRAM.md; install: INSTALL.md; undo: ROLLBACK.md.

## Inside (for fixes)
Source ~/opt/claude-vault (app/vaultd.py HTTP API, app/vaultlib.py secrets/audit/notifier/raw state machine/LLM gate/
policy, app/sources.py tgread/paperless/Firefly, app/gmail.py read-only IMAP + IDLE watcher, Dockerfile,
docker-compose.yml, vault.env, tests/). A fix: edit, run the offline tests here, `vault admin deploy`, then his deploy.
vault-git-sync mirrors ~/bin/{llm-local,secretscan.py,bills} and the tgread/llm-local test suites into the repo.
Tests: `python3 -I ~/opt/claude-vault/tests/test_vault.py` and `tests/test_gmail.py` (fake IMAP server) here; his
deploy runs all suites in the new image (tests/run-all.sh, no network) before the switch. Gmail off: env VAULT_NO_GMAIL=1.
Volume claude-vault_data (/data: home/.secrets 0700, tgread archive, models, cache, audit.log). llama-server
127.0.0.1 inside only, `--cache-ram 0`, weights sha256-checked: Gemma-4-26B-A4B IQ4_XS + MTP drafter on llama.cpp
b11669 (pin = Dockerfile `ARG LLAMA_TAG` + `LLAMA_COMMIT`, the clone must match the commit; bump: check
common/arg.cpp between the tags, then his deploy (image tests), eval_answers --real, llm-local run.sh --real with
LLM_LOCAL_REAL_BACKEND=vault, an embed smoke and ask latency), in /models = read-only bind of
/mnt/data/appdata/claude-vault-models (data pool, NOT docker.img, which is 50 GB and warns at 70%), seeded by his
deploy from ~/opt/llm-models; a Qwen weight file in /data/models is the fallback (switch + rollback:
~/reports/vault/gemma-switch-2026-10-14.md). Runs as uid 10001, read-only root, no capabilities,
12 CPUs / 22 GB (Gemma peaks ~19 GB incl. mmapped weights), cpuset 0-5,8,9,12-15 (VAULT_CPUSET in the server's .env),
llama `-t 6 -tb 12` under `nice 19`: +14 % prompt / +8 % generation over unpinned (~79.8 / 9.24 tok/s), and VM waits
drop 32–88 % while the LLM works. The light VMs run on E-cores (Home Assistant 16-17, Pi-Hole 18-19, Webserver 2
20-21, their emulator threads 16-31; XML backup /mnt/user/backup/vm-xml-2026-10-15/), Win11-emby keeps P-cores 6-7 +
10-11. Alternatives measured (cpuset 0-15 / 16 CPUs: +40 % / +16 % but overlaps Win11-emby; VM-free cores 8,9,12-31:
no gain; 3 P-cores alone: slower): ~/reports/privacy/vault-cpu-pinning-2026-10-15.md; rerun kit
~/opt/llm-bench/cpu-pin/ (llm-idle-wait.sh + vmhealth.sh; bench only while the LLM is idle, it has one slot).
Talks to Telegram only through its OWN bot (never the assistant's: polling would steal it); Bot API methods as listed
under Pinned status.
- Answer quality is scored by `python3 -I ~/opt/claude-vault/tests/eval_answers.py --real` (invented fixtures,
  ~4 min; log in eval_answers.log). Other suites: tests/test_intake.py, tests/test_finance.py,
  tests/test_sessions.py, tgread's ~/opt/tgread-tests/run.sh; his deploy runs them all in the new image.

## tgread inside the vault
- Its config lives in the vault (the vault pins local_llm_cmd, use_local_llm and llm_fallback=false); there is no
  API to change it. Strict mode or other settings: change ~/opt/claude-vault (app/sources.py ensure_config), test,
  `vault admin deploy`, then his deploy.

## Switches and rollback
- Feature switches live in ~/opt/claude-vault/vault.env (`VAULT_NAME=value` lines; a value in the server's .env
  wins). Flip one: edit vault.env, `vault admin deploy`, then `vault deploy request <commit>` (Telegram may deploy
  vault.env changes) or his button. Code rollback of a feature: `git revert` its commits (`git log -- <its file>`),
  then the same deploy path.
- Paperless goes through the vault (`~/.config/vault/mode` = on; check `paperless search x`, `paperless get ID` (a
  card)). `off` would need a fresh local paperless token from him: there is none (a dead copy sits in
  ~/attic/secrets-retired, guarded like a secret). The vault has its own.
- Local LLM: `~/.config/llm-local/backend` = `vault` (~/bin/llm-local runs in the vault via `vault llm-run`; the
  local llama-server is stopped, its boot.sh/crontab lines commented out). Rollback: `rm ~/.config/llm-local/backend;
  llm-server-ctl start` and uncomment those lines (skill llm-local).
- tgread: ~/bin/tgread is a wrapper (`exec vault tg "$@"`); canonical source ~/opt/claude-vault/vendor/tgread.
- Ask sessions (app/sessions.py): `VAULT_ASK_SESSIONS=1`; `0` = --session answers 409 sessions_off, plain asks
  unchanged. Nothing persisted, so no cleanup. Tests: tests/test_sessions.py.
- Person cards (app/people.py): `VAULT_PEOPLE=1`; `0` = /people answers 409 people_off, nothing built. Relation
  questions/relays in the vault bot: `VAULT_PEOPLE_ASK` ("1" = on, owner-approved; "0" turns them off). State
  /data/state/people.db (derived cards + his relations and forget entries); cleanup: delete that file inside the
  vault (cards rebuild at the next read/03:00; relations are lost). Tests: tests/test_people.py, tgread
  PeopleStatsTests.
- Open asks + topics on the cards (app/asks.py + app/topics.py): `VAULT_PEOPLE_ASKS=1`; `0` = cards without asks and
  topics, /people/refresh = 409 people_v2_off, the night worker stops. Night worker (01:00-06:30, background
  priority, own thread): triage of his own DM messages via tgread's vault-internal `out-items` (<= 30 items a call,
  <= 120 calls a night, cursor `out_cursor` in people.db; first run = last 21 days), then a topic calibration when
  none fits the running embed model (Gemma labels ~300 DM chunks inside, numbers only; `vault people calibrate`, job
  op people.refresh). Status: `vault status --json` -> people.open_asks / topics / night. State in people.db: tables
  open_asks, person_topics, topic_protos, meta feed_cursor/out_cursor/topics_cfg/topics_eval; chunk labels in
  index.db chunks.topics (version-tagged). Cleanup: `DELETE FROM open_asks` etc. inside the vault, or drop the
  tables (rebuilt from the feed/cursors). Purge/exclude drop a blend's centroids (back to seeds + recalibration).
  Tests: tests/test_people_v2.py (incl. a poisoning suite), test_index C4IndexTests.test_c5_topic_helpers, tgread
  IndexRowsTests.test_c5_out_items_and_prior.
- Embedding server (app/index.py): `VAULT_EMBED=embeddinggemma`; `0` = no embed server (`vault index eval` still
  works with temporary servers). Weights: the EMBED_* pins in vaultd.py, seeded by his deploy from ~/opt/llm-models
  (or downloaded on Unraid, sha256-checked) into /models. sqlite-vec: Dockerfile stage `vec` (0.1.9, tarball
  sha256), load-tested in the image tests; live: `vault index status`. State: /data/state/index-eval.json (numbers),
  embed-verified.json. Tests: tests/test_index.py (VAULT_VEC_PATH for the sqlite-vec parts), tgread IndexRowsTests.
- HA status feed (app/ha_live.py): `VAULT_HA_FEED=1` in vault.env + `VAULT_HA_DEVICE` (his phone's device id) in the
  server's .env; `0` = /ha/now answers 409 ha_off, events stop, context's location says "not set up". Notes only:
  `echo off > ~/.config/vault-events/ha`. State: /data/state/ha-wifi.json (Wi-Fi class HMACs only). Tests:
  tests/test_ha_live.py (fake HA websocket + REST, the read-only guard), test_vault ApiHa*. Status: `vault status
  --json` → `ha` (feed, state, subscribed, reconnects, error).

## GitHub backup
Source ~/opt/claude-vault is a git repo pushed to its private GitHub repo (profile repos.private_vault): his deploys
fetch from there. `vault admin deploy` runs `vault-git-sync`; cron 03:41 syncs anything else. It refuses to commit
what secretscan flags (inbox note on failure). Never commit .env, data or sessions.

## Changing the guard (it fails closed)
A block is the guard working: use the vault command instead, never work around it. It's a tripwire, not a
sandbox. Blocks are logged by rule name in ~/.local/state/vault-guard/. It fails closed: malformed
input, a crash, a missing script or a timeout blocks the call (the wrapper + `"onFailure": "block"` in settings.json).
So a broken guard blocks every Bash/file call: change it on a copy, run the tests there, then install it. If every
call is blocked "(fail closed)", tell the owner; fix from a container shell: `brain restore .claude/hooks REV`. Tests:
`python3 -I ~/.claude/hooks/test_vault_guard.py`.
