import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, "/home/node/bin/lib")
import botlib  # noqa: E402


def script(d, name, body):
    p = Path(d) / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_atomic_write_creates_dirs_and_leaves_no_tmp(self):
        p = Path(self.d) / "a" / "b" / "x.txt"
        botlib.atomic_write(p, "hé\n")
        botlib.atomic_write(str(p), "two\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "two\n")
        self.assertEqual(os.listdir(p.parent), ["x.txt"])

    def test_json_roundtrip_and_defaults(self):
        p = Path(self.d) / "s" / "state.json"
        self.assertEqual(botlib.load_json(p, {"d": 1}), {"d": 1})
        botlib.save_json(p, {"b": 1, "a": [1]}, indent=1, sort_keys=True)
        self.assertEqual(p.read_text(), '{\n "a": [\n  1\n ],\n "b": 1\n}')
        self.assertEqual(botlib.load_json(p, None), {"a": [1], "b": 1})
        p.write_text("{broken")
        self.assertIsNone(botlib.load_json(p))

    def test_write_inbox_never_overwrites(self):
        ib = Path(self.d) / "inbox"
        f1 = botlib.write_inbox(ib, "cw", "one")
        f2 = botlib.write_inbox(ib, "cw", "two")
        self.assertNotEqual(f1, f2)
        self.assertRegex(f1.name, r"^cw-\d+\.md$")
        self.assertRegex(f2.name, r"^cw-\d+(-\d+)?\.md$")
        self.assertEqual(sorted(p.read_text() for p in ib.iterdir()), ["one", "two"])

    def test_send_tg(self):
        log = Path(self.d) / "sent"
        ok = script(self.d, "tg-ok", f'printf "%s" "$1" > {log}')
        bad = script(self.d, "tg-bad", 'echo "boom: no chat" >&2; exit 3')
        self.assertEqual(botlib.send_tg(ok, "hi there"), (True, ""))
        self.assertEqual(log.read_text(), "hi there")
        self.assertEqual(botlib.send_tg(bad, "x"), (False, "boom: no chat"))
        r = botlib.send_tg(os.path.join(self.d, "missing"), "x")
        self.assertFalse(r[0])
        self.assertLessEqual(len(r[1]), 200)

    def test_lockdown_mode(self):
        for out, want in (("verbs", "verbs"), ("emulate", "emulate"), ("root", "root"), ("junk", "root")):
            self.assertEqual(botlib.lockdown_mode(script(self.d, "us-" + out, f"echo {out}")), want)
        self.assertEqual(botlib.lockdown_mode(os.path.join(self.d, "missing")), "root")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print(f"{n - f} passed, {f} failed")
    sys.exit(1 if f else 0)
