---
name: deadlines
description: The owner's yearly Swiss money and admin deadline radar (his canton and municipality) and tax-document checklist. Use for "what deadlines are coming", "when is my tax return due", "Steuererklärung Frist", "Fristerstreckung", "when do I pay my taxes / Steuerraten", "how much can I pay into 3a", "3a Nachzahlung / Einkauf", "Serafe", "Halbtax renewal", "W-8BEN", "what tax documents am I missing", "Steuerunterlagen", "is my Lohnausweis in paperless", and for ~/inbox or reminder notes tagged deadline:*. Uses ~/bin/deadlines (data with official sources in ~/.config/deadlines/ch-<canton>.json).
---
# Deadline radar (deadlines)

Tool: `deadlines` (~/bin, python3 stdlib). Data: `~/.config/deadlines/ch-<canton>.json` (profile home.canton; dates, amounts per year,
lead times, official sources, `verified` level per item). Owner facts: `~/.config/deadlines/profile.json` (0600,
never send it anywhere): `null` = unknown, so the item shows as "if applicable" and is not synced.

Commands:
- `deadlines list [--days 365] [--all] [--json]`: upcoming deadlines, sorted by act-by date. `--all` adds the
  "if applicable" items and the ones whose date comes from an unknown profile value.
- `deadlines show ID`: dates, action, amounts, planned reminders, sources. IDs: tax-docs, tax-docs-check,
  dbst-due, tax-return, tax-extension-end, tax-rate-1/2/3, pillar3a, kk-switch, kk-franchise-up, serafe,
  halbtax, ga, w8ben.
- `deadlines checklist tax YEAR [--offline] [--json|--md]`: documents for the YEAR tax return, checked against
  paperless (read-only: `paperless search` hits + tier-1 `paperless fields --fields …,period_years`; no
  content). `[x]` found (Lohnausweis: both period years = YEAR; others: dated YEAR by their period years,
  created YEAR/YEAR+1, and the confirm words in title/type/tags), `[?]` probably (the year appears in the dates, a
  "<query> YEAR" full-text hit, or a search hit whose title doesn't confirm), `[ ]` missing. It runs in about 3 s
  offline; a full run makes one `fields` call per hit (the vault reads the file), so run it in the background. `--md` prints a Markdown table for reports (~/reports/admin/tax-YEAR-checklist.md).
- `deadlines sync [--dry-run] [--horizon 200] [--force] [--quiet]`: creates owner reminders
  (`remind add --owner --tag deadline:<id>:<date>:<lead>`) for active items within the horizon. It's idempotent:
  existing tags are kept, changed text or dates are updated, and stale ones are removed. A ledger
  (~/.local/state/deadlines/synced.json) remembers what it created, so a reminder that fired or that the owner
  cancelled is not re-created (`--force` overrides that). It never touches reminders without a `deadline:` tag.
  Covered items (kk-switch → kk-compare) and informational ones (dbst-due, serafe) are never synced.
  Health-insurance switching reminders belong to kk-compare (see its skill).
- Before a trip: `trip-admin [--from D --until D] [--md FILE]` (read-only) lists the deadlines, bills and paperless due
  dates around the trip with the latest safe day before leaving.
- `deadlines validate`: run after editing the data file. It warns when the current or next year's amount is missing.

**Status:** sync is live (owner reminders tagged deadline:*; monthly `deadlines sync --quiet` cron
on the 1st, 09:23). Tax-year-2026 report: ~/reports/admin/tax-2026-checklist.md (to-confirm list; my reminders
tagged tax2026:* on 1 Dec 2026 and 15 Feb 2027).

Answering the owner:
1. Give the date, the action and the one rule that matters, in 2–5 lines. Say "partly verified" where the item
   says so: the canton's 31 March filing date is the standard printed date, but no official canton page states it, so tell him
   to check the date on his form.
2. Facts, not advice: whether to pay 3a, buy in, switch or raise the franchise is his call. Never pay, file or
   contact anyone for him. The online extension (efristerstreckung.sh.ch, PID + date of birth) uses his identity:
   he does it himself.
3. When he tells me a fact, update profile.json (`tax_extension_until`, `pillar3a`, `swiss_broker`,
   `foreign_broker`, `crypto`, `w8ben_signed`, `halbtax_expiry`, `ga_expiry`, `car`, `serafe_invoice_date`,
   `pk_buyin`), then `deadlines sync`, but only once the sync has been approved.
4. Documents he sends for the checklist go to paperless (paperless skill), with a type and tag that fit.

Key facts (sources in the data file):
- Cantonal tax return: due 31 Mar of the following year (partly verified). Online extension up to 30 Nov at most,
  which must be requested before the deadline; longer only in exceptional cases via the Gemeinde.
  eFiling needs the canton's software, receipts attached digitally, and the eFiling code printed on the form.
- Canton/Gemeinde taxes: three provisional instalments due 1 Jun, 1 Sep and 1 Dec (cantonal tax law § 99), each with a
  30-day payment term (StG Art. 178). If instalments 1 and 2 are both unpaid, the whole year falls due.
  Ausgleichszins runs in his favour on payments made before the final bill, and against him from 30 Sep.
  The 2026 rate is not confirmed: 1%/5% has applied since Apr 2024.
- Federal tax: general due date 1 Mar of the following year; pay within 30 days of the bill. Prepaying earns
  0.0% (2026); late interest is 4% (2026 and 2027).
- 3a (with a pension fund): max CHF 7,258 for 2026 and CHF 7,373 for 2027. The money must be CREDITED by 31 Dec,
  so aim for mid-December. Buy-in (Nachzahlung): in force since 2025, and first possible in 2026 for a 2025 gap.
  It only covers gaps from 2025 onwards and goes back at most 10 years. It needs AHV income in both years and
  that year's full maximum paid first. One buy-in per gap year, capped at the small amount. It's deductible
  in the year it's paid.
- Krankenkasse: switching or lowering the franchise must arrive by 30 Nov (kk-compare). Raising the franchise
  needs a written notice by 31 Dec, and BAG recommends mid-December.
- Serafe: CHF 335 (2026), CHF 312 (from 2027), CHF 300 (from 2029). One annual invoice, in a month set by
  one of 12 random billing groups; due within 60 days. The bills tracker handles the payment.
- Halbtax: renews automatically; the invoice comes 2 months before, and he must cancel at least 1 month before
  (swisspass.ch). W-8BEN: valid until 31 Dec of the third year after signing. DA-1 for foreign withholding tax
  goes with the tax return and lapses after 3 years.
- Vehicle tax: not tracked (no car known; `car` in the profile is unknown).

Yearly update (each autumn, once the new 3a maximum is out, usually early October): add the next year's
`amounts`, re-check every source URL (sh.ch pages move), bump `updated` and `checked`, run `deadlines validate`
and ~/opt/deadlines-tests/run.sh (temp state only, fake paperless).
