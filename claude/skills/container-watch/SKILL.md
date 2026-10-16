---
name: container-watch
description: Answer "are my containers up to date", "any security issues / vulnerabilities / CVEs on the server", "is Seerr/Radarr/UniFi/paperless/… vulnerable", "should I update X" for the owner's Unraid server, and handle the weekly container-watch alert notes in ~/inbox. Uses ~/bin/container-watch (read-only inventory + GitHub releases/advisories + NVD).
---
# Container updates & security advisories

**Never update, pull, recreate, restart or re-tag a container without the owner's explicit OK** for that specific change. That includes Unraid's "apply update", `docker pull`, editing a template's Repository/tag, and CA Auto Update settings. The tool and this skill only read.

Tool: `container-watch` (~/bin). It's strictly read-only on the server. One call of the dispatcher's read-only `containers` verb (`unraid-ssh run containers`) reads docker metadata, Unraid's update-status file and an mDNS browse for Home Assistant's version. Installed versions come from the apps' own GET endpoints, or from labels/env as a fallback. Upstream data comes from GitHub releases and repository advisories, NVD (UniFi, Emby, Transmission) and Ubiquiti's firmware API, cached for 7 days in ~/.cache/container-watch.

Answering a question:
1. `container-watch` prints only actionable items (exit 1 if any, 0 and no output if none). It takes a few seconds and uses the cache. Add `--max-age 1d` for fresh upstream data. Don't loop it: GitHub allows 60 unauthenticated requests per hour, and a cold run costs ~40.
2. `container-watch --report` gives the full table: installed vs latest, releases behind, image age, Unraid status, advisories (affecting/total) and notes. `--json` returns everything.
3. Reply in 2–5 lines: what's affected (container, installed → fixed version, severity, a one-line why, link), or "all current, nothing affecting your versions". Don't recite the table unless asked.

What counts as actionable:
- An advisory or CVE whose range includes the installed version. It needs medium+ severity with a stable fix out for at least 3 days, or high/critical severity even with no fix.
- A newer release, out for at least 3 days, whose notes mention security/CVE/GHSA.
- Unraid has shown "update available" for 30+ days while 3+ releases piled up.

Routine version bumps are never actionable. Medium issues fixed only in a beta are report-only (e.g. SABnzbd 5.1.3 XSS, fix in 5.2.0Beta1).

Context:
- CA Auto Update updates all containers daily at 04:00 UTC, so `:latest` containers are usually current.
- The real risks are pinned tags (unifi-controller-reborn runs `11notes/unifi:9.4.19-unraid`, which never updates), Home Assistant (it runs in the "Home Assistant" VM and updates inside HA, not via Docker), and advisories without a fix.
- UniFi 9.4.19 → 10.x is a major upgrade: back up the controller first.
- Repository advisories are often sloppy (open-ended ranges, "All"/"TBD"). An open-ended advisory with no fix version counts as fixed when the installed release is newer than the advisory. The report lists these, plus any advisories it couldn't judge.

Weekly alert (cron Wed 19:13, `--notify --max-age 1d`): silent unless something is actionable. Then it sends ONE `tg` message (≤600 chars) and leaves a note in ~/inbox, once per item; a newer security release re-arms the alert. For an inbox alert note, the owner already has the text: don't resend it, and do nothing unless he asks. If he asks for an update, write a plan first (backup appdata, breaking changes, the exact tag/template change) and wait for his explicit OK before touching anything.

If you tell the owner about an item yourself (in chat), run `container-watch --ack` afterwards so the weekly run doesn't repeat it.

Files: ~/.local/state/container-watch/ (state.json, last.json, last-collect.txt, runs.log, cron.log) and ~/.cache/container-watch/. Testing: `--input last-collect.txt --offline --state-dir DIR --inbox-dir DIR`, `CONTAINER_WATCH_TG=/path/to/stub`. To track a new app, add an entry to `APPS` in the script (image regex, GitHub repo, version sources).
