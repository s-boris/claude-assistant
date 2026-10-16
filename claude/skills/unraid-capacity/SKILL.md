---
name: unraid-capacity
description: Answer "how much space is left", "when will the server/array be full", "how fast is it filling", "is disk X full" for the owner's Unraid server, and handle the weekly capacity-forecast alert notes in ~/inbox. Uses ~/bin/unraid-capacity (read-only daily samples + forecast).
---
# Unraid capacity forecast

Tool: `unraid-capacity` (~/bin). Strictly read-only on the server: one call of the dispatcher's read-only `capacity` verb (`unraid-ssh run capacity`), which reads emhttp's RAM files and `df` (statfs) and wakes no disk. I can't write to the server; anything that frees space (deleting media, moving data, adding a disk) is the owner's call.

Answering a question:
1. `unraid-capacity forecast --human` reads the stored samples instantly. Add `--live` for to-the-minute numbers (one extra `capacity` verb call, about a second; nothing saved). The samples are taken daily at 03:17.
2. Reply in 2–4 lines: free space (TB), usable free above the media share's ~102 GB per-disk minimum, the 30-day growth rate, and the projected full date. Add the 90-day date if it differs a lot. Round the numbers ("~9.4 TB free, filling ~120 GB/day → full around mid-December").
3. Per-disk questions: the "Disks over 90%" lines. "trend" comes from that disk's own samples (needs 7+ days); "high-water model" is Unraid's allocator simulated with the array's rate. Under high-water, near-full disks fill together near the array's date, not one by one.
4. `unraid-capacity forecast` (no flag) prints the full JSON (pools, docker.img, excluded one-off deletes, trend details).

Caveats worth mentioning when relevant: until ~30 days of real samples exist, the growth before that is estimated from the Radarr/Sonarr import/delete ledger (media only). One-off big deletes are left out of the rate. Pool and docker.img trends need 6+ days of samples.

The owner knows the array is ~92% full (disk1 ~96%, 8–9 disks over 90%). Don't bring it up unprompted; the weekly alert covers getting worse.

Weekly alert (cron Mon 09:13, `forecast --alert --notify`): silent unless the array is projected full within 60 days or a pool/docker.img within 14. Then it texts the owner (`tg`) and leaves an inbox note, once per band (array 60/30/14/7 days, pools 14/7/3; it re-arms after the ETA recovers past 1.25× the threshold). For an inbox alert note, the owner already has the text. Offer concrete options only if asked, or if it's urgent (≤14 days): which arr upgrades/profiles drive growth, old seeding torrents, recycle-bin size, or a bigger or extra disk (spending money: their decision). An inbox note saying "samples are stale" means `record` is failing: check `~/.local/state/unraid-capacity/cron.log` and `unraid-capacity record --dry-run`; don't tell the owner unless it can't be fixed.

Files: ~/.local/state/unraid-capacity/ (samples.jsonl, backfill.jsonl + backfill-meta.json, alerts.json, cron.log). Re-run the estimate with `unraid-capacity backfill` (Radarr/Sonarr GETs only, ~20 s); it stays anchored to the first real sample.
