#!/usr/bin/python3 -I
"""Tests for ~/bin/recall. Everything runs in temp dirs (RECALL_HOME, SECRETSCAN_SOURCES); the fake secrets are
assembled at runtime so no file here contains one. The real ~/.local/state/recall is never touched."""
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import unittest

RECALL = os.environ.get("RECALL_BIN", "/home/node/bin/recall")
HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "session.jsonl")
PROJ = ".claude/projects/-home-node-workspace"
FAKE_VALUE = "zq" + "FAKEtok" + "7" * 12 + "XyZ"            # matched by value (from the temp ~/.secrets)
FAKE_BOT = "123456789:" + "AA" + "k" * 33                   # matched by pattern (telegram-bot-token)


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="recall-tests.")
        self.h = os.path.join(self.t, "home")
        for d in ("workspace", "reports/research", "reports/vault", PROJ + "/memory", ".secrets",
                  ".local/state/context", "inbox"):
            os.makedirs(os.path.join(self.h, d), exist_ok=True)
        self.w("workspace/CHANGELOG.md", "# Changelog\n\n- 2026-10-09: Added the gnu tracker.\n"
               "- 2026-10-11 14:05: Switched the heron cache to sqlite.\n  - sub note about heron\n")
        self.w("workspace/TODO.md", "# TODO\n## In progress\n- **Yak shaving** — running\n\nPlain paragraph about tapir.\n")
        self.w("reports/research/2026-10-10-notes.md", "# Research\n\nThe gecko benchmark was slow.\n")
        self.w(PROJ + "/memory/owner.md", "Owner likes the margay colour scheme.\n")
        self.w(".secrets/test.json", json.dumps({"api_token": FAKE_VALUE}))
        # never-index paths
        self.w(".local/state/context/telegram.md", "private chat about ocelot\n")
        self.w(".local/state/context/signals.md", "signal about ocelot\n")
        self.w(".local/state/context/signals.json", '{"signals": [{"type": "plan", "topic": "ocelot"}]}\n')
        self.w("workspace/signals.json", '{"note": "copied signals about ocelot"}\n')
        self.w("reports/vault/raw.md", "vault raw text about ocelot\n")
        os.symlink(os.path.join(self.h, ".secrets/test.json"), os.path.join(self.h, "reports/leak.md"))
        os.symlink(os.path.join(self.h, ".local/state/context/telegram.md"), os.path.join(self.h, "workspace/tg.md"))
        shutil.copy(FIXTURE, os.path.join(self.h, PROJ, "s1.jsonl"))
        self.env = dict(os.environ, RECALL_HOME=self.h, SECRETSCAN_SOURCES=self.h)
        self.env.pop("RECALL_STATE", None)
        self.env.pop("RECALL_NOW", None)
        self.db = os.path.join(self.h, ".local/state/recall/index.db")

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def w(self, rel, text, mode="w"):
        p = os.path.join(self.h, rel)
        with open(p, mode) as f:
            f.write(text)
        return p

    def run_r(self, *args, env=None, rc=None):
        p = subprocess.run([RECALL, *args], env=env or self.env, capture_output=True, text=True, timeout=60)
        if rc is not None:
            self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        return p

    def q(self, *args):
        return self.run_r(*args).stdout

    def index(self):
        return self.run_r("--index", rc=0).stdout

    def count(self, sql="SELECT COUNT(*) FROM docs"):
        with sqlite3.connect(self.db) as db:
            return db.execute(sql).fetchone()[0]

    def ids(self):
        with sqlite3.connect(self.db) as db:
            return [r[0] for r in db.execute("SELECT id FROM docs ORDER BY id")]

    def append_jsonl(self, rec, newline=True):
        with open(os.path.join(self.h, PROJ, "s1.jsonl"), "a") as f:
            f.write(json.dumps(rec) + ("\n" if newline else ""))

    # ---------------------------------------------------------------- md
    def test_md_dated_bullet_and_location(self):
        self.index()
        out = self.q("heron")
        self.assertIn("2026-10-11 14:05 workspace/CHANGELOG.md:4", out)
        self.assertIn("sub note about heron", out)  # indented sub-bullet stays with its item

    def test_md_sources_and_dates(self):
        self.index()
        self.assertIn("2026-10-10 reports/research/2026-10-10-notes.md", self.q("gecko"))  # date from file name
        self.assertIn(PROJ + "/memory/owner.md", self.q("margay"))
        self.assertIn("workspace/TODO.md", self.q("tapir"))
        self.assertIn("workspace/TODO.md", self.q("yak"))

    def test_never_indexed_paths(self):
        self.index()
        self.assertIn("no hits", self.q("ocelot"))
        with open(self.db, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"ocelot", raw)
        self.assertNotIn(b"api_token", raw)  # the symlink into ~/.secrets was not followed

    # ---------------------------------------------------------------- chat
    def test_chat_kept_parts(self):
        self.index()
        out = self.q("zanzibar")
        self.assertIn("2026-10-09 10:00 chat/owner", out)  # UTC -> Zurich
        self.assertIn("chat/owner tg#42", self.q("quokka"))
        self.assertIn("[button] Approve walrus plan", self.q("walrus"))
        self.assertIn("chat/me ", self.q("narwhal"))
        self.assertIn("chat/me>tg", self.q("axolotl"))
        out = self.q("ibex")
        self.assertIn("chat/agent", out)
        self.assertNotIn("<usage>", out)
        self.assertIn("lemur", self.q("lemur"))

    def test_chat_skipped_parts(self):
        self.index()
        for word in ("pangolin", "platypus", "wombat", "capybara", "okapi"):
            self.assertIn("no hits", self.q(word), word)
        with open(self.db, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"QUJDQUJDQUJD", raw)
        self.assertNotIn(b"iVBORw0KGgo", raw)

    def test_secrets_redacted(self):
        self.w("workspace/NOTES.md", "- 2026-10-12: key is %s and bot %s for dingo\n" % (FAKE_VALUE, FAKE_BOT))
        self.append_jsonl({"type": "user", "timestamp": "2026-10-12T10:00:00Z", "message": {
            "role": "user", "content": "use token %s for the dingo bot %s" % (FAKE_VALUE, FAKE_BOT)}})
        self.index()
        with open(self.db, "rb") as f:
            raw = f.read()
        self.assertNotIn(FAKE_VALUE.encode(), raw)
        self.assertNotIn(FAKE_VALUE.lower().encode(), raw)
        self.assertNotIn(b"k" * 33, raw)
        out = self.q("dingo", "-n", "5")
        self.assertEqual(out.count("[redacted]"), 4, out)
        self.assertNotIn(FAKE_VALUE, out)

    def test_fail_closed_without_secretscan(self):
        env = dict(self.env, RECALL_SECRETSCAN_DIR=os.path.join(self.t, "nothing-here"))
        p = self.run_r("--index", env=env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("fail-closed", p.stderr)
        self.assertEqual(self.count(), 0)
        p = self.run_r("--index", "--cron", env=env)
        self.assertEqual(len(os.listdir(os.path.join(self.h, "inbox"))), 1)

    # ---------------------------------------------------------------- incremental
    def test_jsonl_incremental_append_and_partial_line(self):
        self.index()
        n0 = self.count()
        first_ids = self.ids()
        rec = {"type": "assistant", "timestamp": "2026-10-12T09:00:00Z",
               "message": {"role": "assistant", "content": [{"type": "text", "text": "the jackal job finished"}]}}
        self.append_jsonl(rec, newline=False)  # still being written
        self.index()
        self.assertEqual(self.count(), n0)
        self.assertIn("no hits", self.q("jackal"))
        with open(os.path.join(self.h, PROJ, "s1.jsonl"), "a") as f:
            f.write("\n")
        out = self.index()
        self.assertIn("+1 chunks", out)
        self.assertEqual(self.count(), n0 + 1)
        self.assertEqual(self.ids()[:n0], first_ids)  # old rows untouched
        self.assertIn("2026-10-12 11:00 chat/me", self.q("jackal"))
        self.assertIn("+0 chunks from 0 changed", self.index())

    def test_jsonl_rewritten_is_reindexed(self):
        self.index()
        p = os.path.join(self.h, PROJ, "s1.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-10-12T09:00:00Z",
                                "message": {"role": "user", "content": "only the kudu now"}}) + "\n")
        self.index()
        self.assertIn("no hits", self.q("zanzibar"))
        self.assertIn("kudu", self.q("kudu"))

    def test_md_change_and_delete(self):
        self.index()
        self.w("workspace/TODO.md", "# TODO\n- **Bison task** — new\n")
        os.remove(os.path.join(self.h, "reports/research/2026-10-10-notes.md"))
        self.index()
        self.assertIn("no hits", self.q("tapir"))
        self.assertIn("no hits", self.q("gecko"))
        self.assertIn("Bison", self.q("bison"))

    def test_auto_refresh_when_stale(self):
        self.index()
        with sqlite3.connect(self.db) as db:
            last = float(db.execute("SELECT v FROM meta WHERE k='last_index'").fetchone()[0])
        self.w("workspace/GOALS.md", "- 2026-10-12: Goal about the eland project\n")
        env = dict(self.env, RECALL_NOW=str(last + 10 * 60))
        self.assertIn("no hits", self.run_r("eland", env=env).stdout)  # 10 min old: no refresh
        env = dict(self.env, RECALL_NOW=str(last + 31 * 60))
        self.assertIn("workspace/GOALS.md", self.run_r("eland", env=env).stdout)

    def test_first_query_builds_index(self):
        self.assertFalse(os.path.exists(self.db))
        self.assertIn("CHANGELOG", self.q("heron"))

    # ---------------------------------------------------------------- search & output
    def test_filters(self):
        self.index()
        self.assertIn("no hits", self.q("heron", "--src", "chat"))
        self.assertIn("CHANGELOG", self.q("heron", "--src", "md"))
        self.assertIn("no hits", self.q("zanzibar", "--src", "md"))
        self.assertIn("no hits", self.q("heron", "--since", "2026-10-12"))
        self.assertIn("CHANGELOG", self.q("heron", "--since", "2026-10-11"))
        self.assertEqual(self.run_r("x", "--since", "12.10.2026").returncode, 2)

    def test_natural_question_and_fallback(self):
        self.index()
        self.assertIn("chat/me", self.q("what did we decide about the narwhal migration?"))
        out = self.q("narwhal gnu")  # no chunk has both: falls back to either
        self.assertIn("narwhal", out)
        self.assertIn("gnu", out)
        self.assertIn("CHANGELOG", self.q('"heron cache"'))
        self.assertIn("CHANGELOG", self.q("her*"))
        self.assertIn("CHANGELOG", self.q("heron NOT gecko", "--raw"))
        self.assertIn("bad query", self.run_r('"(', "--raw").stderr)

    def test_output_is_short(self):
        lines = "\n".join("- 2026-10-%02d: item %d about the serval rollout %s" % (1 + i % 28, i, "x " * 200)
                          for i in range(120))
        self.w("reports/serval.md", "# Serval\n" + lines + "\n")
        self.index()
        out = self.q("serval", "-n", "500")
        rows = out.strip().split("\n")
        self.assertLessEqual(len(rows), 41)
        self.assertEqual(len(rows), 41)
        self.assertTrue(all(len(r) < 320 for r in rows), max(len(r) for r in rows))
        self.assertLessEqual(len(self.q("serval").strip().split("\n")), 11)

    def test_by_date(self):
        self.index()
        out = self.q("heron OR gnu", "--raw", "--by-date").strip().split("\n")[1:]
        self.assertIn("2026-10-11", out[0])
        self.assertIn("2026-10-09", out[1])

    def test_show_with_context(self):
        self.index()
        hit = [l for l in self.q("narwhal").split("\n") if l.startswith("#")][0]
        cid = hit.split()[0][1:]
        out = self.q("--show", cid, "-C", "1")
        self.assertIn(">> #" + cid, out)
        self.assertIn("s1.jsonl"[:2], out)
        self.assertIn("Decided: the narwhal migration runs nightly at 03:00.", out)
        self.assertEqual(sum(l.startswith("-- #") for l in out.split("\n")), 2, out)  # one neighbour before, one after
        self.assertEqual(self.run_r("--show", "99999").returncode, 1)

    def test_permissions_and_stats(self):
        self.index()
        self.assertEqual(stat.S_IMODE(os.stat(self.db).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.db)).st_mode), 0o700)
        self.assertRegex(self.q("--stats"), r"recall index: \d+ md chunks \(4 files\), \d+ chat chunks \(1 transcripts\)")

    def test_rebuild_is_idempotent(self):
        self.index()
        n = self.count()
        self.run_r("--rebuild", rc=0)
        self.assertEqual(self.count(), n)


if __name__ == "__main__":
    unittest.main(verbosity=1)
