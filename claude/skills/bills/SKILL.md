---
name: bills
description: Track the owner's bills (Swiss QR-bills, invoices, eBill, Mahnungen) with due-date reminders. Use when the owner sends a photo or PDF of a bill or invoice ("Rechnung", "QR-Rechnung", "Mahnung"; normally to the vault bot, see the skill), asks "what do I have to pay" / "bills this week", says "I paid X", or when the daily paperless bill scan adds bills. Admin and bills are his #1 annoyance, so be quick and low-friction.
---
# Bills autopilot

Tool: `bills` (~/bin/bills, state in ~/.local/state/bills/). Reminders go out automatically via `remind --owner` (3 days before and on the due day, 09:00) and are cancelled when the bill is marked paid.

## A bill comes in (privacy plan D)
- **Normal path:** he sends it to the **vault bot** (his 🔐 chat), or a mail bill's PDF goes in with `vault gmail
  to-paperless REF [N]`. The vault files it and replies "📄 Filed as #N · type · CHF x · due …"; its feed event makes
  `vault-events` run `bills scan-paperless --cron --since <today-2d> --filed N` (no tokens), which adds the bill from
  the tier-1 fields (amount, currency, due date, payee) and sets the reminders. A `bills-paperless-*` note marked
  "vault bot intake" means he already has the bot's line: message him only about the "to check" / "check …" items.
- **Sent to me by mistake** (a photo or PDF of a bill here): never `bills extract` it and never Read it. File it
  privately (skill paperless, "Owner sends a file": `paperless upload … --tag Inbox --tag private --create`, delete
  the local file, "🔒 Filed privately → #N; next time the vault bot 📥"), then `bills scan-paperless --since <today>`.
- Never ask him for the IBAN or the reference: he pays from the QR code. The tracker keeps payee, amount, due date and
  `has_payment_ref`; IBAN/reference are tier-2 fields that stay in the vault.
- Confirm a bill I added myself in ONE line, e.g. `🧾 Swisscom CHF 59.90, due Sat 31.10 → reminder Wed 28.10 (paperless #37)`.
- `bills add --payee P --amount A --currency C --due YYYY-MM-DD [--paperless ID] --source …` for one he tells me about
  in words (eBill, "I got a bill from X for 80, due Friday"). It deduplicates.

## A bill that is already in paperless
`bills from-paperless ID` prints `bills extract`-style JSON from the vault's tier-1 fields (amount, currency, due date,
payee, qr_bill, has_payment_ref; no IBAN/reference, no document text). Low-confidence fields: check them with him
(amount/due date only). Then `bills add … --paperless ID --source pdf`.

## Autopilot: new bills in paperless (cron 08:41)
`bills scan-paperless --cron` checks documents added since the last scan (read-only on paperless, through the vault):
skips docs already linked to a bill and obvious non-bills by metadata (payslips, contracts, receipts), reads the
vault's tier-1 fields + has_payment_ref for the rest (`has_pay = has_payment_ref or iban or reference`), and adds a
bill when there's a QR code or bill words (title/type/tags or a full-text hit: Rechnung, Mahnung, Zahlteil, zahlbar
bis, Invoice…) plus amount + due date. Missing data → "to
check", never guessed; due >14 days past → skipped as probably paid. Bills get `--source paperless --source-id
paperless:ID` (dedupes with Gmail/photo bills, filling in the paperless link).
- Daily cron: an ~/inbox note `bills-paperless-*` arrives ONLY when bills were added: tell the owner in ONE short
  message (payee, amount, due; "Mahnung" and "check due/payee" flags matter), and ask about the "to check" ones in
  the same message (`bills from-paperless ID`). A `bills-paperless-error-*` note = 3 failed runs: check `vault status`.
- Vault bot intake (`--filed IDS`, started by vault-events): the note comes when bills were added or one of those
  documents needs checking; it says what to tell him (only the to-check / flagged items; the rest needs no message).
- By hand: `bills scan-paperless --dry-run -v` (what it would do), `--since YYYY-MM-DD` re-checks older documents,
  `--json`. State: ~/.local/state/bills/paperless-scan.json, log paperless-scan.log. Tests:
  `python3 -I ~/opt/bills-tests/test_scan_paperless.py`.

## Other requests
- "What falls due while I'm away?": `trip-admin` (read-only; bills, deadlines, paperless due dates, latest safe day).
- "What do I have to pay?": `bills week` (due within 7 days, plus overdue) or `bills list --open`.
- "I paid Swisscom" / "paid #3": `bills paid PAYEE|ID`. That cancels the reminders. Confirm with 👍.
- Corrections: `bills edit ID --due …`; mistakes: `bills remove ID`.

## Rules
- Never pay anything yourself. Paying is the owner's job (eBanking/TWINT). The stored card needs his explicit per-purchase OK and isn't for bills.
- No bills from Gmail: the owner declined bill fields from mail; mail bills go into paperless with `vault gmail to-paperless`, and the scan picks them up.
- Bill documents stay out of my context: the vault bot or the private paperless path, never `bills extract` or Read on his bills.
