#!/usr/bin/env python3
"""Tests for `bills scan-paperless` with a mocked `paperless` command (no vault, no real reminders).
Run: python3 -I ~/opt/bills-tests/test_scan_paperless.py"""
import json, os, shutil, subprocess, sys, tempfile, textwrap, unittest

BILLS = os.environ.get("BILLS_BIN") or os.path.expanduser("~/bin/bills")   # BILLS_BIN: test a copy
NOW = "2026-10-12 08:41"

MOCK = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import json, os, sys
    fx = json.load(open(os.environ["MOCK_FIX"]))
    with open(os.environ["MOCK_LOG"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\n")
    cmd = sys.argv[1]
    if cmd in fx.get("fail", []):
        print("vault unreachable", file=sys.stderr); sys.exit(1)
    if cmd == "recent":
        n = int(sys.argv[sys.argv.index("-n") + 1])
        docs = sorted(fx["docs"], key=lambda d: d["added"], reverse=True)[:n]
        print(json.dumps({"results": docs}))
    elif cmd == "search":
        q = sys.argv[2]
        ids = fx["dun_hits"] if q.startswith("(Mahnung") else fx["content_hits"]
        print(json.dumps({"count": len(ids), "results": [{"id": i} for i in ids]}))
    elif cmd == "fields":
        f = fx["fields"].get(sys.argv[2])
        if f is None:
            print("fields failed", file=sys.stderr); sys.exit(1)
        print(json.dumps(f))
    else:
        sys.exit(2)
''').lstrip()

REMIND = "#!/bin/sh\necho \"$@\" >> \"$MOCK_REMIND_LOG\"\necho ok\n"


def doc(i, title, added="2026-10-11", dtype=None, tags=(), corr=None):
    return {"id": i, "created": added, "added": added, "title": title, "correspondent": corr,
            "document_type": dtype, "tags": list(tags), "url": f"http://x/documents/{i}/details"}


def fields(amount=None, due=None, iban=None, ref=None, payee=None, qr=False, cur="CHF", conf=None, legacy=False):
    """What the vault answers for the tier-1 SCAN_FIELDS (plan B): IBAN/reference never leave it, only
    has_payment_ref. legacy=True: an older vault that still sent iban/reference."""
    f = {"amount": amount, "currency": cur, "due_date": due, "payee": payee, "invoice_date": None, "qr_bill": qr,
         "has_payment_ref": bool(iban or ref), "correspondent": None, "title": None, "document_type": None,
         "_confidence": conf or {}, "_warnings": []}
    if legacy:
        del f["has_payment_ref"]
        f.update(iban=iban, reference=ref)
    return f


IBAN = "CH4431999123000889012"          # SIX example QR-IBAN
QRR = "210000000003139471430009017"     # SIX example QR reference


def fixture():
    return {
        "docs": [
            doc(40, "Scan 2026-10-11"),                                            # QR bill, no metadata words
            doc(41, "Lohnabrechnung Oktober 2026", dtype="Lohnabrechnung", tags=["Lohnabrechnung"]),
            doc(42, "Rechnung Swisscom 2026-10"),                                  # title word + IBAN, no QR
            doc(43, "Brief Vermieter"),                                            # content hit, amount, no IBAN
            doc(44, "Scan 2026-10-10", added="2026-10-10"),                        # Mahnung (content), QR
            doc(45, "Spende Stiftung"),                                            # QR slip without amount
            doc(46, "Rechnung Zahnarzt 2025"),                                     # old, due long past
            doc(47, "Rechnung Sunrise"),                                           # already tracked
            doc(48, "Quittung Migros"),                                            # receipt
            doc(49, "Newsletter"),                                                 # nothing
            doc(50, "Rechnung EKS", corr="EKS"),                                   # low-confidence due
            doc(51, "Rechnung Salt"),                                              # matches an existing gmail bill
            doc(30, "Rechnung alt", added="2026-06-01"),                           # before the window
        ],
        "content_hits": [43, 44, 49],
        "dun_hits": [44],
        "fields": {
            "40": fields("59.90", "2026-10-31", IBAN, QRR, "Swisscom (Schweiz) AG", qr=True),
            "42": fields("120.00", "2026-11-05", "CH9300762011623852957", None, "Swisscom AG"),
            "43": fields("1450.00", "2026-11-01"),
            "44": fields("85.00", "2026-10-20", IBAN, QRR, "Serafe AG", qr=True),
            "45": fields(None, None, IBAN, None, "Stiftung X", qr=True),
            "46": fields("200.00", "2025-12-01", IBAN, QRR, "Dr. Zahn", qr=True),
            "48": fields("12.00", "2026-10-11"),
            "49": fields(),
            "50": fields("310.40", "2026-11-12", "CH9300762011623852957", None, None, conf={"due_date": "low"}),
            "51": fields("49.95", "2026-10-25", None, None, "Salt Mobile SA"),
            "30": fields("75.00", "2026-10-30", IBAN, QRR, "Alt AG", qr=True),
        },
    }


class ScanPaperless(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="bills-scan-test-")
        self.state, self.inbox = os.path.join(self.t, "state"), os.path.join(self.t, "inbox")
        self.fix, self.log = os.path.join(self.t, "fix.json"), os.path.join(self.t, "calls.log")
        self.rlog = os.path.join(self.t, "remind.log")
        mock, rem = os.path.join(self.t, "paperless"), os.path.join(self.t, "remind")
        for path, body in ((mock, MOCK), (rem, REMIND)):
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.env = dict(os.environ, BILLS_STATE_DIR=self.state, BILLS_INBOX=self.inbox, BILLS_PAPERLESS=mock,
                        BILLS_REMIND=rem, BILLS_NOW=NOW, MOCK_FIX=self.fix, MOCK_LOG=self.log,
                        MOCK_REMIND_LOG=self.rlog, REMIND_DB=os.path.join(self.t, "remind.db"))
        self.write_fix(fixture())
        self.bills("add", "--payee", "Sunrise", "--amount", "70", "--due", "2026-10-28", "--paperless", "47")
        self.bills("add", "--payee", "Salt Mobile SA", "--amount", "49.95", "--due", "2026-10-25",
                   "--source", "gmail", "--source-id", "msg-1")
        open(self.log, "w").close()

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def write_fix(self, fx):
        with open(self.fix, "w") as f:
            json.dump(fx, f)

    def bills(self, *args, check=True):
        r = subprocess.run([sys.executable, BILLS, *args], capture_output=True, text=True, env=self.env, timeout=60)
        if check and r.returncode not in (0, 3):
            self.fail(f"bills {args} → {r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def scan(self, *args, check=True):
        return self.bills("scan-paperless", *args, check=check)

    def calls(self, cmd=None):
        with open(self.log) as f:
            c = [json.loads(x) for x in f if x.strip()]
        return [x for x in c if cmd is None or x[0] == cmd]

    def db(self):
        with open(os.path.join(self.state, "bills.json")) as f:
            return json.load(f)["bills"]

    def notes(self):
        return sorted(os.listdir(self.inbox)) if os.path.isdir(self.inbox) else []

    def results(self, *args):
        r = self.scan("--json", *args)
        return {x["id"]: x for x in json.loads(r.stdout)["results"]}

    # ------------------------------------------------------------------ classification
    def test_dry_run_verdicts_and_no_side_effects(self):
        res = self.results("--dry-run")
        v = {i: r["verdict"] for i, r in res.items()}
        self.assertEqual(v, {40: "bill", 41: "skip", 42: "bill", 43: "review", 44: "bill", 45: "review",
                             46: "skip", 47: "skip", 48: "skip", 49: "skip", 50: "bill", 51: "bill"})
        self.assertNotIn(30, res)                                    # added before the 60-day first-run window
        self.assertIn("already tracked as bill #1", res[47]["why"])
        self.assertIn("old bill", res[46]["why"])
        self.assertIn("no amount / due date (QR slip)", res[45]["why"])
        self.assertIn("no QR code / IBAN / reference", res[43]["why"])
        self.assertTrue(res[44]["bill"]["dunning"])
        self.assertEqual(res[50]["bill"]["check"], ["due"])
        self.assertEqual(res[50]["bill"]["payee"], "EKS")            # correspondent when the QR has no payee
        self.assertEqual(len(self.db()), 2)
        self.assertFalse(os.path.exists(os.path.join(self.state, "paperless-scan.json")))
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.calls("add"), [])

    def test_metadata_skips_need_no_field_reads(self):
        self.scan("--dry-run")
        read = {int(c[1]) for c in self.calls("fields")}
        self.assertFalse(read & {41, 47, 48, 30}, read)               # payslip, tracked, receipt, out of window
        self.assertEqual(read, {40, 42, 43, 44, 45, 46, 49, 50, 51})

    def test_search_is_limited_to_the_window(self):
        self.scan("--dry-run")
        qs = [c[1] for c in self.calls("search")]
        self.assertEqual(len(qs), 2)
        self.assertTrue(all(q.endswith("added:[2026-08-13 to now]") for q in qs), qs)

    # ------------------------------------------------------------------ adding
    def test_real_run_adds_dedupes_and_notes(self):
        r = self.scan("--cron")
        self.assertEqual(r.returncode, 0, r.stderr)
        bills = {b["paperless"]: b for b in self.db() if b.get("paperless")}
        self.assertEqual(set(bills), {40, 42, 44, 47, 50, 51})
        b40 = bills[40]
        self.assertEqual((b40["payee"], b40["amount"], b40["due"], b40["source"], b40["source_id"]),
                         ("Swisscom (Schweiz) AG", "59.90", "2026-10-31", "paperless", "paperless:40"))
        self.assertEqual((b40.get("ref"), b40.get("iban")), (None, None))      # tier 2 stays in the vault
        self.assertIn("Mahnung", bills[44]["note"])
        self.assertIn("check due", bills[50]["note"])
        salt = [b for b in self.db() if b["payee"] == "Salt Mobile SA"]
        self.assertEqual(len(salt), 1)                               # deduped against the gmail bill
        self.assertEqual(salt[0]["paperless"], 51)                   # ... which got the paperless link
        self.assertEqual(len(self.db()), 6)
        self.assertIn("bill:3", open(self.rlog).read())              # reminders scheduled through remind
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        text = open(os.path.join(self.inbox, notes[0])).read()
        self.assertIn("4 new bill(s)", text)
        self.assertIn("Swisscom (Schweiz) AG CHF 59.90", text)
        self.assertIn("Mahnung", text)
        self.assertIn("paperless #45", text)                         # review items ride along
        self.assertNotIn("Salt", text)                               # existed already: not "new"
        for secret in (IBAN, QRR, "CH9300762011623852957"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, r.stdout)

    def test_second_run_skips_decided_docs_and_stays_silent(self):
        self.scan("--cron")
        open(self.log, "w").close()
        r = self.scan("--cron")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls("fields"), [])
        self.assertEqual(len(self.notes()), 1)
        self.assertIn("added:[2026-10-10 to now]", self.calls("search")[0][1])   # last scan - 2 days
        self.assertEqual(len(self.db()), 6)

    def test_new_doc_next_day_only(self):
        self.scan("--cron")
        fx = fixture()
        fx["docs"].append(doc(52, "Rechnung Galaxus", added="2026-10-13"))
        fx["fields"]["52"] = fields("399.00", "2026-11-12", IBAN, QRR, "Digitec Galaxus AG", qr=True)
        self.write_fix(fx)
        open(self.log, "w").close()
        self.env["BILLS_NOW"] = "2026-10-13 08:41"
        self.scan("--cron")
        self.assertEqual([c[1] for c in self.calls("fields")], ["52"])
        self.assertEqual(len(self.notes()), 2)
        self.assertEqual(self.db()[-1]["payee"], "Digitec Galaxus AG")

    def test_no_bills_no_note(self):
        fx = fixture()
        fx["docs"] = [d for d in fx["docs"] if d["id"] in (41, 48, 49)]
        self.write_fix(fx)
        r = self.scan("--cron")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notes(), [])
        self.assertIn("0 bill(s)", r.stdout)

    def test_since_rechecks_seen_docs_without_duplicates(self):
        self.scan()
        r = self.results("--since", "2026-01-01")
        self.assertEqual(r[30]["verdict"], "added")                  # older doc now inside the window
        self.assertEqual(r[40]["verdict"], "skip")                   # linked to a bill in the first run
        self.assertEqual(len([b for b in self.db() if b.get("paperless") == 40]), 1)

    # ------------------------------------------------------------------ failures
    def test_field_error_retried_next_run(self):
        fx = fixture()
        del fx["fields"]["40"]
        self.write_fix(fx)
        r = self.scan("--cron", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error: paperless #40", r.stdout)
        st = json.load(open(os.path.join(self.state, "paperless-scan.json")))
        self.assertIsNone(st["last_scan"])                           # window not advanced
        self.assertNotIn("40", st["docs"])
        self.write_fix(fixture())
        open(self.log, "w").close()
        self.scan("--cron")
        self.assertEqual([c[1] for c in self.calls("fields")], ["40"])
        self.assertIn(40, {b.get("paperless") for b in self.db()})

    def test_vault_down_three_times_one_note(self):
        fx = fixture()
        fx["fail"] = ["recent"]
        self.write_fix(fx)
        for _ in range(4):
            r = self.scan("--cron", check=False)
            self.assertEqual(r.returncode, 1)
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("bills-paperless-error"))
        self.write_fix(fixture())
        self.scan("--cron")
        st = json.load(open(os.path.join(self.state, "paperless-scan.json")))
        self.assertEqual(st["failures"], 0)

    def test_human_output(self):
        r = self.scan("--dry-run", "-v")
        out = r.stdout
        self.assertIn("would add: Swisscom (Schweiz) AG CHF 59.90, due Sat 31.10", out)
        self.assertIn("to check: paperless #45", out)
        self.assertIn("skip: paperless #41", out)
        self.assertNotIn(IBAN, out)


    # ------------------------------------------------------------------ privacy plan B: tier-1 fields only
    def test_requests_tier1_fields_only(self):
        self.scan("--dry-run")
        for c in self.calls("fields"):
            want = set(c[c.index("--fields") + 1].split(","))
            self.assertIn("has_payment_ref", want)
            self.assertFalse(want & {"iban", "reference", "reference_type", "payee_address", "payment_message"}, c)

    def test_legacy_iban_still_counts_as_payment_ref(self):
        fx = fixture()
        fx["fields"]["42"] = fields("120.00", "2026-11-05", "CH9300762011623852957", None, "Swisscom AG", legacy=True)
        fx["fields"]["43"] = fields("1450.00", "2026-11-01", None, None, None, legacy=True)
        self.write_fix(fx)
        res = self.results("--dry-run")
        self.assertEqual(res[42]["verdict"], "bill")
        self.assertTrue(res[42]["bill"]["has_payment_ref"])
        self.assertEqual(res[43]["verdict"], "review")
        self.assertNotIn("CH9300762011623852957", json.dumps(res))

    def test_has_payment_ref_alone_makes_a_bill(self):
        res = self.results("--dry-run")
        self.assertTrue(res[42]["bill"]["has_payment_ref"])              # title word + payment slip, no QR
        self.assertFalse(res[51]["bill"]["has_payment_ref"])

    # ------------------------------------------------------------------ vault bot intake (--filed, from vault-events)
    def test_filed_review_only_writes_a_note(self):
        fx = fixture()
        fx["docs"] = [d for d in fx["docs"] if d["id"] in (41, 45, 49)]   # 45: QR slip without amount
        self.write_fix(fx)
        r = self.scan("--cron", "--since", "2026-10-10", "--filed", "45")
        self.assertEqual(r.returncode, 0, r.stderr)
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        text = open(os.path.join(self.inbox, notes[0])).read()
        self.assertIn("vault bot", text)
        self.assertIn("To check", text)
        self.assertIn("paperless #45", text)
        self.assertIn("never ask him for IBAN/reference", text)

    def test_filed_review_of_other_doc_stays_silent(self):
        fx = fixture()
        fx["docs"] = [d for d in fx["docs"] if d["id"] in (41, 45, 49)]
        self.write_fix(fx)
        r = self.scan("--cron", "--since", "2026-10-10", "--filed", "49")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.notes(), [])                               # 45 is not one he just filed

    def test_filed_added_bill_note_says_no_message(self):
        fx = fixture()
        fx["docs"] = [d for d in fx["docs"] if d["id"] in (40, 41)]
        self.write_fix(fx)
        r = self.scan("--cron", "--since", "2026-10-10", "--filed", "40")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = open(os.path.join(self.inbox, self.notes()[0])).read()
        self.assertIn("1 bill(s) added", text)
        self.assertIn("No message needed", text)
        self.assertIn("Swisscom (Schweiz) AG CHF 59.90", text)
        self.assertEqual(self.db()[-1]["source_id"], "paperless:40")

    def test_filed_rejects_bad_ids(self):
        r = self.scan("--cron", "--filed", "abc", check=False)
        self.assertEqual(r.returncode, 2)

    def test_from_paperless_vault_mode_tier1(self):
        vlog = os.path.join(self.t, "vault.log")
        vault = os.path.join(self.t, "vault")
        with open(vault, "w") as f:
            f.write("#!/bin/sh\necho \"$@\" >> " + vlog + "\n"
                    "echo '{\"amount\": \"59.90\", \"currency\": \"CHF\", \"due_date\": \"2026-10-31\", "
                    "\"payee\": \"Swisscom\", \"qr_bill\": true, \"has_payment_ref\": true, \"_confidence\": {}}'\n")
        os.chmod(vault, 0o755)
        self.env.update(VAULT_MODE="on", BILLS_VAULT=vault)
        r = self.bills("from-paperless", "40")
        out = json.loads(r.stdout)
        self.assertTrue(out["has_payment_ref"])
        self.assertIsNone(out["iban"])
        self.assertNotIn("--iban", out["suggested_add"])
        asked = open(vlog).read().split("--fields ")[1].split()[0].split(",")
        self.assertIn("has_payment_ref", asked)
        self.assertFalse(set(asked) & {"iban", "reference", "reference_type", "payee_address", "payment_message"})

if __name__ == "__main__":
    unittest.main(verbosity=1)
