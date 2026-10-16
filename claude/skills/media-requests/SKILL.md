---
name: media-requests
description: Request movies and TV shows for the owner's media server (Seerr → Radarr/Sonarr → Emby on his Unraid server), report download status, and answer read-only library questions. Use for "add/download/request/get <movie or show>", "is X downloaded / available yet", "how far is X", "what's downloading", "what's new on Emby", "what's airing / coming out this week", "what's missing / stuck", "what needs upgrading", "are Sonarr/Radarr/Seerr ok", "what's trending", "something like X / recommendations", "what's eating the space / biggest shows / cleanup candidates", "what does nobody watch / unwatched / what can I delete", and for button presses with callback data `media_req:…`.
---
# Media requests (Seerr 3.x :5055, Radarr :7878, Sonarr :8989; Emby plays it)

Tool: `media` (~/bin/media). Keys live in ~/.secrets/media.json; the script reads them. Never print or paste them.

| Command | Does |
|---|---|
| `media search "title" [--type movie\|tv] [-n 8]` | `Title (year) [type] tmdb:ID -- status` |
| `media request TMDB_ID --type movie\|tv [--seasons all\|1,2] [--dry-run]` | requests via Seerr; refuses if already available/requested and prints the status instead |
| `media status "title" [--type T] [-n 1]` or `--tmdb ID --type T` | Seerr status, open requests, per-season state, Emby link, live queue progress/ETA and import problems |
| `media queue` | everything downloading in Radarr/Sonarr, % and ETA, flags stuck imports (`!!`) |
| `media recent [-n 15]` | recently imported movies and episodes |

Read-only extras (GET only; they never request, change or delete anything). All take `--json`. Exit codes: 0 ok, 1 error, 2 health problems, 3 partial (one service failed; the rest is still printed, plus `!! skipped …`).

| Command | Does |
|---|---|
| `media airing [--days 7] [--cinema]` | Sonarr calendar (Zurich time, collapsed multi-episode drops) + Radarr digital/physical releases (`--cinema` adds cinema dates); ✓ = already on disk |
| `media missing [-n 10]` | monitored but no file: shows by episode count, released movies longest-wanted first, not-yet-out movies with their next digital/physical date |
| `media cutoff [-n 10]` | below the quality profile's cutoff (upgrade candidates), per show with profile and cutoff; movies with current quality |
| `media health [--quiet]` | Sonarr/Radarr `/health`, Seerr status (restart needed, can't reach an arr); one line `all fine: …` when nothing's wrong, `--quiet` prints nothing then |
| `media trending [--type T] [-n 10] [--new]` | TMDB trending via Seerr, numbered, with server status; `--new` = not on the server or requested |
| `media similar "title" [--type T] [-n 10] [--new]` or `--tmdb ID --type T` | TMDB recommendations (falls back to "similar") with server status |
| `media hogs [--top 20] [--type T] [--sort size\|stale]` | disk used/free, library totals, movie size by quality, biggest titles with GB/episode, file/added dates and who requested them; `stale` = size × years since anything new arrived |
| `media unwatched [--top 15] [--type T] [--days 180] [--min-age 90] [--report [FILE]] [--no-emby] [--json]` | what nobody watches: titles on disk > 90 d with no play in 180 d, ranked by size × idle time (last play by anyone, plays 90/365 d, viewers, requester), reclaimable TB at 180 d / 365 d / never, stale seasons of big shows still watched. History = Tracearr (since 25.08.2026) + Emby's Playback Reporting DB before that (since 02.2020, read-only over ssh). `--report` writes the full table to ~/reports/media/unwatched-DATE.md |

Statuses come from Seerr: not requested, requested (pending/processing), partially available, available, blocklisted, deleted. TV requests skip seasons that are already available or requested, and specials (S0) are never requested.

## "Add / download X"
1. `media search "X"` (add `--type` if they said movie/show/series).
2. Decide:
   - One clear match (exact title, or they gave a year, or the top hit is obviously it): request it straight away.
   - Ambiguous (remakes, same-name movie and show, several plausible years): `reply` with 2–4 buttons, one per row, text `Title (year)` (add "· series" for TV), `data` = `media_req:<tmdb>:<movie|tv>`.
   - Already available / requested: don't request; say so (with the Emby link from `media status` if available).
3. `media request <tmdb> --type <t>`. TV defaults to all missing seasons; pass `--seasons` if they asked for specific ones.
4. Confirm in one line, e.g. `🎬 Requested Dune: Part Two (2024), Radarr will grab it` / `📺 Requested The Bear S1–3, Sonarr will grab it`. A 👍 reaction on their message is fine in addition, not instead.

Button press `media_req:<tmdb>:<type>` → step 3 directly.

Ready pings (no tokens): cron runs `media ready-watch` every 10 min and texts him `🎬 Title (year) is ready on Emby` once one of HIS requests (his Seerr user, profile services.seerr_user) is available, plus `📺 Show S02E03–E05 ready` for new episodes of shows he requested (upgrades skipped; held during 00–08). So after a request, don't promise to check back: he'll get a ping. `media ready-watch --status` shows the state, pending pings and failures. Seerr's own Telegram agent only posts "available" for everyone, silently, to a group chat, not to his DM.

No extra confirmation is needed for requests: it's the owner's own server and Seerr auto-approves (the API key acts as his admin user). Removing, deleting or blocklisting anything always needs the owner's explicit OK first, and the script has no such command on purpose.

## "Is X downloaded?" / "what's downloading?"
- `media status "X"` → one or two lines: available (+ Emby link), or downloading NN% with ETA, or requested/waiting for release. Unreleased titles show as requested (processing) with no queue entry. That's normal: say it'll download when released.
- `media queue` → a short list. `!! importBlocked`/`importFailed`: `arr-unblock --cron` (*/15) imports it by itself when the *arr's parser verifies every file; otherwise its ~/inbox note offers `arr-unblock --approve svc:QUEUEID` (only after his OK). Mention it briefly; I'm read-only on arr settings unless asked.
- Wrong-movie grabs are dropped and re-grabbed by `arr-mismatch grabs --cron`; suspected wrong files already on disk are notes only (`arr-mismatch list`). See JOBS.md "Media".
- `media recent` for "what's new".

## Library questions (read-only)
- "What's on this week / when's the next X": `media airing` (or `--days 14`). Reply with a short day-by-day list; skip daily filler shows unless asked.
- "What's missing / stuck": `media missing`. Released movies that are still missing after months usually have no usable release; say so, don't re-request.
- "Is everything OK with Sonarr/Radarr": `media health`. Problems need the owner or the arr UI (I'm read-only on arr settings).
- "What's trending / what should I watch / something like X": `media trending --new` or `media similar "X" --new`, then offer `media_req:<tmdb>:<type>` buttons for the ones they might want.
- "What's eating the space / cleanup ideas": `media hogs` (and `--type movie`, `--sort stale`). The array is nearly full (`unraid-capacity forecast --human`), so this matters: point out high GB/episode shows (remux-sized TV), the Remux-2160p movie total, and old ended shows nobody added to in years, with who requested them. Deleting, downgrading or unmonitoring anything is the owner's call: pitch it, never do it (there's no delete command on purpose). `media cutoff` lists pending upgrades, which grow files further. For "what does nobody watch / what could go": `media unwatched --report` (2-3 s), then send the short version (reclaimable at 180/365 d/never + top few with last play and requester) and offer the report; per-person viewing is other people's data, so give counts, not who watched what.

## Notes
- Search results include people; the script filters them out.
- Others use the same Seerr too (e.g. Timon, Päde). Requests show who asked.
- Offline tests (fake Seerr/Radarr/Sonarr, GET-only and no-key-leak checks): `~/opt/media-tests/run.sh`. Run them after changing ~/bin/media.
