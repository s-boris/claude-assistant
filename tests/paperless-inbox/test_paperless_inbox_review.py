#!/usr/bin/env python3
"""Tests for ~/bin/paperless-inbox-review with a mocked `paperless` incl. the vault's `suggest` (no vault, no writes).
Run: python3 -I ~/opt/paperless-inbox-tests/test_paperless_inbox_review.py"""
import json, os, shutil, subprocess, tempfile, textwrap, unittest

TOOL = os.environ.get("PINB_BIN") or os.path.expanduser("~/bin/paperless-inbox-review")   # PINB_BIN: test a copy
SECRET = "ZZ-CONTENT-MARKER-77"          # must never show up in any output

MOCK_PL = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import json, os, sys
    fx = json.load(open(os.environ["MOCK_FIX"]))
    with open(os.environ["MOCK_LOG"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\n")
    cmd = sys.argv[1]
    if cmd in fx.get("fail", []):
        print("vault unreachable", file=sys.stderr); sys.exit(1)
    if cmd == "recent":
        print(json.dumps({"results": fx["docs"]}))
    elif cmd == "rules":
        print(json.dumps(fx["rules"]))
    elif cmd == "suggest":                       # the vault: existing names only, never content
        if os.environ.get("MOCK_SUGGEST_FAIL"):
            print("vault: local model busy", file=sys.stderr); sys.exit(1)
        i = sys.argv[2]
        sug = fx["suggest"].get(i, {"document_type": None, "correspondent": None, "tags": []})
        print(json.dumps({"id": int(i), "current": {}, "suggested": sug, "changes": {}, "_warnings": []}))
    elif cmd == "get":
        print("get must not be called (plan B: no content)", file=sys.stderr); sys.exit(9)
    elif cmd == "set":
        print(json.dumps({"id": int(sys.argv[2]), "title": "x", "document_type": "Rechnung", "correspondent": None,
                          "tags": ["Rechnung"], "changed": ["document_type", "tags"]}))
    else:
        sys.exit(2)
''').lstrip()



def doc(i, title, tags=(), dtype=None, added="2026-10-10"):
    return {"id": i, "created": "2026-10-01", "added": added, "title": title, "correspondent": None,
            "document_type": dtype, "tags": list(tags), "url": f"http://x/documents/{i}/details"}


def rule(i, name, inbox=False, alg="any", match=""):
    return {"id": i, "name": name, "document_count": 1, "algorithm": alg, "match": match, "is_insensitive": True,
            "is_inbox_tag": inbox}


def fixture():
    return {
        "docs": [
            doc(1, "Lohnabrechnung Northwind 2026-08", ["Lohnabrechnung", "Northwind"], "Lohnabrechnung", "2026-09-01"),
            doc(2, "Scan 2026-10-09", ["Inbox"]),                  # Swisscom bill (LLM), content mentions Northwind? no
            doc(3, "Praktikumszeugnis"),                            # untagged, title alias -> existing type
            doc(4, "Brief privat", ["Inbox", "private"]),           # private: never fetched
            doc(5, "Arbeitsvertrag Contoso", ["Arbeitsvertrag"], "Arbeitsvertrag"),   # filed, not a candidate
            doc(6, "Lohn Sept", ["Inbox"], added="2026-10-11"),     # LLM -> existing type + employer tag
        ],
        "rules": {
            "tags": [rule(10, "Inbox", inbox=True), rule(11, "Lohnabrechnung"), rule(12, "Northwind"),
                     rule(13, "Arbeitszeugnis"), rule(14, "Arbeitsvertrag"), rule(15, "private")],
            "document_types": [rule(20, "Lohnabrechnung"), rule(21, "Arbeitszeugnis"), rule(22, "Arbeitsvertrag"),
                               rule(23, "Rechnung")],
            "correspondents": [rule(30, "Swisscom"), rule(31, "Northwind AG")],
        },
        # what the vault's suggest answers (it read the content inside the vault; only names come out)
        "suggest": {"2": {"document_type": "Rechnung", "correspondent": "Swisscom", "tags": ["Telekom"]},
                    "4": {"document_type": "Rechnung", "correspondent": None, "tags": [SECRET]},
                    "6": {"document_type": "Lohnabrechnung", "correspondent": "Northwind AG", "tags": []}},
    }


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for name, body in (("paperless", MOCK_PL),):
            p = os.path.join(self.d, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)
        self.fx = os.path.join(self.d, "fx.json")
        self.write_fx(fixture())
        self.log = os.path.join(self.d, "log")
        self.inbox = os.path.join(self.d, "inbox")
        self.env = dict(os.environ, PINB_PAPERLESS=os.path.join(self.d, "paperless"),
                        PINB_STATE_DIR=os.path.join(self.d, "state"),
                        PINB_INBOX=self.inbox, PINB_NOW="2026-10-18", MOCK_FIX=self.fx, MOCK_LOG=self.log)

    def tearDown(self):
        shutil.rmtree(self.d)

    def write_fx(self, fx):
        with open(self.fx, "w") as f:
            json.dump(fx, f)

    def run_tool(self, *args, env=None):
        return subprocess.run([TOOL, *args], capture_output=True, text=True, env=env or self.env, timeout=60)

    def calls(self):
        with open(self.log) as f:
            return [json.loads(x) for x in f]

    def review(self):
        r = self.run_tool("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return {d["id"]: d for d in json.loads(r.stdout)["docs"]}, r

    def test_candidates_and_suggestions(self):
        docs, r = self.review()
        self.assertEqual(list(docs), [6, 4, 3, 2])                  # inbox-tagged + untagged, newest first (then id)
        self.assertEqual(docs[3]["suggest"]["document_type"], {"name": "Arbeitszeugnis", "new": False})
        self.assertEqual(docs[3]["suggest"]["confidence"], "high")
        self.assertEqual([t["name"] for t in docs[3]["suggest"]["tags"]], ["Arbeitszeugnis"])
        s2 = docs[2]["suggest"]
        self.assertEqual(s2["document_type"], {"name": "Rechnung", "new": False})     # the vault: existing names
        self.assertEqual(s2["correspondent"], {"name": "Swisscom", "new": False})
        self.assertIn("vault", s2["basis"])
        self.assertEqual(s2["remove_tags"], ["Inbox"])
        self.assertNotIn("Telekom", [t["name"] for t in s2["tags"]])   # LLM may not invent tags
        s6 = docs[6]["suggest"]
        self.assertEqual(s6["document_type"]["name"], "Lohnabrechnung")
        self.assertIn("Northwind", [t["name"] for t in s6["tags"]])    # employer tag from the correspondent
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertFalse([c for c in self.calls() if c[0] == "get"])  # plan B: never a content/card read
        self.assertIn(["suggest", "2", "--json"], self.calls())

    def test_private_never_fetched(self):
        docs, _ = self.review()
        self.assertFalse([c for c in self.calls() if c[0] in ("get", "suggest") and c[1] == "4"])
        self.assertIn("private", docs[4]["suggest"]["basis"])
        self.assertIsNone(docs[4]["suggest"]["document_type"])
        n = len(self.calls())
        self.review()                                                   # private: complete, cached
        self.assertFalse([c for c in self.calls()[n:] if c[0] == "suggest"])

    def test_plain_and_limit(self):
        r = self.run_tool("--plain", "-n", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(lines[0], "paperless inbox: 2 to review (of 4)")
        self.assertTrue(lines[1].startswith('#6 2026-10-01 "Lohn Sept" [inbox] → type Lohnabrechnung'))
        self.assertNotIn(SECRET, r.stdout)

    def test_cron_note_only_when_nonempty(self):
        r = self.run_tool("--cron")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.inbox), ["paperless-inbox-review.md"])
        note = open(os.path.join(self.inbox, "paperless-inbox-review.md")).read()
        self.assertIn("pinb:ok:<id>", note)
        self.assertIn('"Praktikumszeugnis"', note)
        self.assertNotIn(SECRET, note)
        shutil.rmtree(self.inbox)
        fx = fixture()
        fx["docs"] = [d for d in fx["docs"] if d["id"] in (1, 5)]
        self.write_fx(fx)
        r = self.run_tool("--cron")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.inbox))

    def test_cron_failure_writes_note(self):
        fx = fixture()
        fx["fail"] = ["recent"]
        self.write_fx(fx)
        r = self.run_tool("--cron")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed", open(os.path.join(self.inbox, "paperless-inbox-review.md")).read())

    def test_skip(self):
        r = self.run_tool("skip", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        docs, _ = self.review()
        self.assertNotIn(3, docs)
        self.assertIn(3, [d["id"] for d in json.loads(self.run_tool("--json", "--all").stdout)["docs"]])
        self.assertIn("1 skipped", self.run_tool("--plain").stdout)

    def test_apply_gate_and_write(self):
        self.review()
        for args in (["apply", "2"], ["apply", "2", "--confirm", "pinb:ok:3"], ["apply", "2", "--confirm", "yes"]):
            r = self.run_tool(*args)
            self.assertEqual(r.returncode, 2, args)
        self.assertFalse(any(c[0] == "set" for c in self.calls()))
        r = self.run_tool("apply", "2", "--confirm", "pinb:ok:2")
        self.assertEqual(r.returncode, 0, r.stderr)
        sets = [c for c in self.calls() if c[0] == "set"]
        self.assertEqual(sets, [["set", "2", "--type", "Rechnung", "--correspondent", "Swisscom", "--tag=-Inbox",
                                 "--json"]])                         # existing names: no --create
        r = self.run_tool("apply", "2", "--confirm", "pinb:ok:2")      # suggestion consumed
        self.assertEqual(r.returncode, 1)

    def test_apply_refuses_stale(self):
        self.review()
        fx = fixture()
        fx["docs"][1]["title"] = "Rechnung Swisscom 2026-10"          # someone filed it meanwhile
        self.write_fx(fx)
        r = self.run_tool("apply", "2", "--confirm", "pinb:ok:2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed since the review", r.stderr)
        self.assertFalse(any(c[0] == "set" for c in self.calls()))

    def test_suggest_failure_falls_back_and_retries(self):
        env = dict(self.env, MOCK_SUGGEST_FAIL="1")
        r = self.run_tool("--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        docs = {d["id"]: d for d in json.loads(r.stdout)["docs"]}
        self.assertEqual(docs[3]["suggest"]["document_type"]["name"], "Arbeitszeugnis")   # title heuristics work
        self.assertIsNone(docs[2]["suggest"]["document_type"])         # no content alias any more (plan B)
        n = sum(1 for c in self.calls() if c[0] == "suggest")
        self.review()                                                   # incomplete -> recomputed by the vault
        self.assertGreater(sum(1 for c in self.calls() if c[0] == "suggest"), n)
        n = sum(1 for c in self.calls() if c[0] == "suggest")
        self.review()                                                   # complete -> cached, no new suggest
        self.assertEqual(sum(1 for c in self.calls() if c[0] == "suggest"), n)

    def test_no_llm_skips_suggest(self):
        r = self.run_tool("--json", "--no-llm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in self.calls() if c[0] == "suggest"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
