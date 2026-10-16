---
name: kk-compare
description: Swiss health insurance (Krankenkasse, Grundversicherung/KVG) premium comparison and switching for the owner. Use for "which Krankenkasse is cheapest", "should I switch health insurance", "Prämien 2027", "Franchise 300 or 2500", telmed/HMO/Hausarzt model questions, "draft my Krankenkasse Kündigung", the Nov 30 switching deadline, and the kk-2027 reminder notes in ~/inbox. Uses ~/bin/kk-compare (official BAG premium data, read-only).
---
# Health insurance comparison (kk-compare)

Tool: `kk-compare` (~/bin, python3 stdlib). Data: the official BAG premium CSV (opendata.bagnet.ch,
dataset health-insurance-premiums), catchment areas, tariff names and the priminfo region file,
cached in ~/.cache/kk and refreshed after 30 days (`--refresh` forces it, `--info` shows provenance).
The owner's defaults are in ~/.config/kk/profile.json (0600, never send it anywhere): canton,
premium region, municipality (BFS number), age group (birth year), accident cover (off when an employer's UVG covers accidents). `current` holds his policy (insurer, franchise, model, premiums). Name and home address
for the cancellation letter come from the assistant profile (~/.config/assistant/profile.json; a key set in
the kk profile wins); canton/BFS fall back to it too.

Commands:
- `kk-compare [--franchise 2500] [--model all|standard|hmo|hausarzt|praxis|telmed|flex|managed] [-n 15]`
  ranks the offers, with CHF/month, CHF/year and worst case/year (premium + franchise + 700 co-pay).
  `--current-insurer X --current-premium P` marks his insurer and adds the yearly delta.
- `kk-compare --franchise-matrix`: cheapest offer per franchise × model, plus the break-even yearly
  medical costs vs. franchise 2500.
- `kk-compare --costs [--medical 0,1000,5000] [--current-premium P --current-franchise F]`: yearly
  totals for medical-cost scenarios, best franchise per model.
- `kk-compare --letter --insurer X --policy N [--keep-vvg] --out ~/reports/kk-2027-kuendigung-draft.txt`:
  cancellation letter draft (BAG sample wording, Einschreiben). It only writes a file.
- Other cantons/people: `--canton ZH --region 2`, `--plz 8000`, `--bfs N`, `--age young|child`,
  `--accident yes`; `--region-of PLZ`; `--insurers` (BAG numbers, legal names, addresses); `--json`.

Answering the owner:
1. Run the matching command and give 3–6 lines of facts: the cheapest options per model, the yearly
   difference, and the deadline. Facts, not advice: switching is his call.
2. Always say what the cheaper models require: telmed = call/app first before any doctor visit
   (except emergencies, gynaecology, eye doctor, usually paediatrics); HMO = a fixed health centre
   first; Hausarzt = your chosen GP first; flex = choose among GP/telmed/pharmacy. Skipping the
   gatekeeper can mean the insurer refuses to pay. Basic cover is legally identical everywhere.
3. HMO vs. Hausarzt is my split of the BAG type PRAXIS by tariff name; check the insurer's terms
   for anything that matters.
4. The cost model ignores the CHF 15/day hospital contribution and the 40% co-pay on drugs with
   cheaper generics.

Switching rules (BAG guide 2027): insurers send the new premium by 31 Oct; the cancellation must
ARRIVE by 30 Nov (last working day before it when that's a weekend); send it registered by mid-Nov.
Join the new insurer at the same time: no health questions in basic insurance, and the old cover
only ends once the new insurer confirms. Unpaid premiums dunned by 30 Nov block the switch.
Supplementary insurance (VVG) is separate, with its own notice periods. Raising the franchise at
the same insurer: tell them by mid-Dec; lowering it or leaving a managed model: by 30 Nov.

Never send the letter or contact an insurer: the owner prints, signs and sends it himself. Signing
up with a new insurer uses his identity: his decision, his action.

Status:
- His policy is in the kk profile (`current`). For anything missing, ask the vault first (`vault find "Krankenkasse"`,
  `paperless search "Police"`: cards and tier-1 fields only), then ask him.
- Findings: ~/reports/research/kk-2027.md.
- The kk-2027 reminders were removed on purpose (2027 is settled). Don't re-create them. Re-check next autumn when the BAG publishes 2028 premiums.
- Tests: ~/opt/kk-tests/run.sh (fixture + full cross-check of his canton against the BAG PDF guide).
Each autumn (from late Sep) the BAG publishes the next year's data at the same URL; `--refresh` and
rerun the tests (the PDF check needs that year's wegweiser_<canton>-<year>.pdf in ~/.cache/kk/ref).
