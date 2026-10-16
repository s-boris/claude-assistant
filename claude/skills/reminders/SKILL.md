---
name: reminders
description: Set, list or cancel reminders for the owner, one-off or recurring ("remind me Friday 18:00 to …", "erinnere mich morgen an …", "every Monday at 9 …", "jeden Montag um 9 …", "what reminders do I have", "cancel the dentist reminder"). Owner reminders are sent to Telegram at the exact minute by cron, with no tokens and no need for me to be awake.
---
# Owner reminders (`~/bin/remind`)

Times are Europe/Zurich, format `YYYY-MM-DD HH:MM`. `remind --help` has every option.

## Set
- `remind add --owner "2026-10-16 18:00" "Pick up the dry cleaning"`
- Relative dates ("tomorrow", "Friday", "in 2 hours"): work them out from `date`.
- A weekday name means the next one; a time without a date means today, or tomorrow if it has passed.
- No time given: ask, or pick 09:00 for a day-only reminder and say so.
- The text is what he'll read ("⏰ <text>"): short, in his language.
- Confirm with 👍 or one line with the exact time ("⏰ Fri 16 Oct, 18:00"), especially if I interpreted something.

## Recurring
- `remind add --owner --every RULE [--until YYYY-MM-DD] "first YYYY-MM-DD HH:MM" "text"`
- RULE: `daily`, `weekdays`, `weekends`, `weekly` (the first date's weekday), `weekly:mon,thu` (or just `"mon,thu"`; German `mo,di,mi,do,fr,sa,so` works), `monthly` (same day), `monthly:15` / `monthly:31` (short months: last day) / `monthly:last`, `yearly`, `every:3d` / `every:2w` (or `3d`, `2w`).
- Examples: "jeden Montag um 9" → `--every weekly:mon "<next Monday> 09:00"`; "every weekday at 7:30" → `--every weekdays`; "jeden Monat am Letzten" → `--every monthly:last`; "alle zwei Wochen" → `--every 2w`; "bis Ende Jahr" → `--until 2026-12-31`.
- The first datetime is the first occurrence; it snaps forward to the first matching day, and a past start moves to the next future slot.
- Zurich wall time is kept across DST. After downtime it fires once, not once per missed slot.
- Confirm with the rule and first time: "⏰ every Mon 09:00, first Mon 12 Oct".

## List, skip, cancel
- `remind list`: numbered; recurring ones end in "(every Mon)". Show him only `[owner]` entries; the rest are my own follow-ups.
- `remind cancel N` (N from the current list) ends a one-off or a whole series.
- `remind cancel --match TEXT` removes every reminder whose text contains TEXT ("cancel the dentist reminder"); list first if the match could hit more than one.
- `remind skip N` skips only the next occurrence of a series ("no gym reminder this Thursday").
- `remind cancel --tag TAG` is for scripts (bills uses `bill:<id>`, deadlines `deadline:*`): don't remove those by hand unless he asks.

## Delivery
- Cron runs `remind due` every minute; owner reminders go out via `tg`.
- If sending fails, the reminder lands in ~/inbox for me to deliver.
