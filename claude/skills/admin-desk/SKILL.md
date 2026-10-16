---
name: admin-desk
description: Write the owner's Swiss business letters as print-ready PDFs and fill or sign PDF forms. Use for "write a Kündigung", "cancel my X contract (letter)", "kündige mein Abo / meinen Vertrag", "Einsprache" (e.g. against a tax assessment / Veranlagungsverfügung), "Widerruf", "write a letter to …", "ask the Steueramt for …", "fill this form", "füll das Formular aus", "sign this PDF", "unterschreib das", and when he sends a PDF form or a photo of his signature. Uses ~/bin/letter and ~/bin/pdfform. Nothing is ever sent by mail or post; he prints and sends.
---
# Admin desk: letters and PDF forms

Tools (venv ~/opt/letter-venv, persisted by ~/boot.sh). Run `letter --help` or `pdfform --help` for every flag.
- `letter new --type kuendigung|einsprache|widerruf|generic --to "Name\nStreet\nPLZ Ort" …` writes a PDF and a .txt copy to ~/reports/letters/ (or `--out`). The layout follows SN 010130: sender block, a C5/6 or DL window (`--window right` by default, or `left`), "Exampletown, 10. Oktober 2026", a bold subject, the salutation, "Freundliche Grüsse", and fold marks. `--lang en` for English.
- `letter template TYPE …` prints only the body, so you can edit it and pass it back as `--body-file`.
- `pdfform fields | fill | sign | text | grid` handles forms (pypdf + pikepdf).
- Sender data (name, home address) comes from the assistant profile (~/.config/assistant/profile.json; `profile-get`, never print values); ~/.config/kk/profile.json and then ~/.config/letter/sender.json override it (create that file only if he wants a phone number or email on his letters).

## Letter flow (always: draft → his OK → PDF → file to him)
1. **Facts.** Find out who it goes to, which contract or decision, and the customer, policy or reference number.
   - Look in paperless first (`paperless search "Salt"`, then `paperless get ID`: a card with date, correspondent, type, title). The customer, contract or policy number and the addresses are tier-2 fields: `paperless fields ID --fields customer_number,contract_number,policy_number,sender_address --reason "you asked me to cancel your Salt contract: the letter needs your customer number and Salt's address"` → his ✅ in the vault bot → `vault raw release RID`. Use the recipient's official address for cancellations; the contract, AGB or their website (read-only) names it. No ✅: leave a `[Kundennummer]` placeholder and ask him.
   - Ask him only for what you can't find. Never invent numbers or dates.
2. **Draft.** Run `letter new … --preview`. It prints the whole letter and the notes: deadline, suggested `--send-by`, and any placeholders still to fill. Send him the text with `reply`, plus the one or two notes that matter (e.g. "must arrive by Sat 31.10"). Add buttons: "✅ PDF" / "✏️ Change". `[Placeholders]` block the final PDF, so fill them first or ask him for the values.
3. **PDF.** After his OK (or a 👍 reaction), run the same command without `--preview`. Send the PDF with `reply` `files`, together with one line on how to send it: print it, sign by hand, and post it as Einschreiben by <date>.
   - Offer `--file` (paperless) and `--send-by YYYY-MM-DD` (an owner reminder 2 days before) as buttons on the draft message, so one OK covers everything.
   - `--draft` gives a watermarked ENTWURF PDF if he wants to see the layout first.
4. Corrections: rerun with the changed flags or body. Each run overwrites the same output path.

### Types and what the notes compute
| Type | Key flags | Deadline rule (in the notes) |
|---|---|---|
| kuendigung | `--contract "Mobile-Abo Salt" --effective YYYY-MM-DD\|next --notice-months N` | Must ARRIVE by end date minus notice. For private contracts, receipt counts, not the postmark. The letter says "per X, sonst auf den nächstmöglichen Termin", asks "Bitte bestätigen Sie mir die Kündigung schriftlich", and is marked Einschreiben. |
| einsprache | `--decision "Veranlagungsverfügung Staats- und Gemeindesteuern 2025" --decision-date … --received … --antrag "…" --reasons-file F --encl "Kopie Verfügung"` | 30 days from receipt; Sat/Sun move it to Monday. For an authority, posting on the last day is enough. Address it to the authority named in the Rechtsmittelbelehrung, with a concrete Antrag and a Begründung. |
| widerruf | `--contract … --contract-date … --basis "Art. 40a ff. OR"` | 14 days for doorstep/phone sales (Art. 40a ff. OR) and consumer credit (Art. 16 KKG); posting on the last day counts. Online purchases have no statutory right in CH. |
| generic | `--subject … --body-file F` | none |

- Body markup: blank line = new paragraph, `**bold**`, a line with only `**Heading**` becomes a heading, `- ` lines become a list.
- Kündigung body from a file: the confirmation sentence is added automatically if it's missing.
- Other options: `--ref "Kundennummer: 123"` (repeatable; goes under the subject), `--encl` for enclosures, `--salutation "Sehr geehrter Herr Muster"`, `--closing`, `--date`, `--no-registered`.
- Krankenkasse (KVG) Grundversicherung: `kk-compare --letter` prints the BAG model letter; you can reuse its body paragraphs as `--body-file`. Or use `--type kuendigung --contract "Grundversicherung (OKP)" --effective YYYY-12-31 --notice-months 1`, which makes it arrive by 30 Nov.

## Forms
1. Get the PDF: `download_attachment`.
2. `pdfform fields X.pdf` lists every field: type, current value, checkbox/radio states, options and position in mm. `pdfform fields X.pdf --template > data.json` gives a skeleton to fill.
3. Fill the values you know: his name, address and birth date from the kk profile, and anything already in paperless (`paperless get` card; identifiers and addresses via `paperless fields … --reason`, which needs his ✅). Ask him for the rest. Forms he sends me to fill and his signature photo are mine to read (unlike bills and letters, which go to the vault bot). Never guess IDs, AHV numbers or bank details. Show him the filled values (short list) for an OK before you fill.
4. `pdfform fill X.pdf --data data.json --out Y.pdf`. Add `--flatten` when he'll print or send it as is; leave the fields editable otherwise. Checkboxes take true/false, radio groups one of the listed states. An unknown field name fails with "did you mean …".
5. **Scans / PDFs without fields:**
   - `pdfform grid X.pdf --page N --out grid.png`, then Read the PNG to pick positions: mm from the top-left; red labels are x, blue are y.
   - Put the text under `"_overlay": [{"page":1,"x":30,"y":52,"text":"Alex Doe","size":10}]`, or use `pdfform text … --pos x,y --text …`. `y` is the text baseline.
   - Check the result visually (`pdftoppm -r 70 -png -singlefile Y.pdf /tmp/…/check`, then Read) before sending it to him.
6. Send it back with `reply` `files`. `--file` and `--send-by` work here too.

## Signature
- `letter signature status` says whether an image exists. If he wants signed PDFs, ask him for a photo of his signature: dark pen on white paper, flat and well lit.
- Then run `letter signature import PHOTO`. It removes the paper background, makes the image transparent, trims it, and stores it as ~/.config/letter/signature.png with mode 0600. Show him a `--draft` letter with `--sign` to check the result.
- The image is private: never send it anywhere else and never print it into anything he didn't ask for.
- Use it with `letter new … --sign` or `pdfform sign X.pdf --page N --pos x,y [--width 45] --out Y.pdf` (position = top-left corner of the image).
- Legal: a pasted image is not a handwritten signature (Art. 14 OR). Where written form is required (Kündigung of rental or employment contracts, Einsprache, anything else that must be signed), he signs the printout by hand. The tools print this reminder.

## Rules
- Nothing goes out by email or post from here. Pingen (a postal letter API) would be a later opt-in; it spends money, so it needs his explicit OK for each letter.
- paperless: if the tag "Korrespondenz" doesn't exist, `--file` files without a tag and says so. Ask him once before using `--create-tag`. Type "Kündigung" exists and is set automatically for Kündigungen. Never file drafts.
- His letters are private: keep them in ~/reports/letters, and send only what he asked for.
- Tests: `~/opt/letter-tests/run.sh` (stub paperless, temp reminders). Samples are in ~/opt/letter-tests/samples/.
