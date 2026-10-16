#!/usr/bin/env python3
"""Tests for ~/bin/vault-events with a fake vault client and a fake bills (temp HOME, no vault, no tokens).
Run: python3 -I ~/opt/vault-events-tests/test_vault_events.py   (VE_BIN=path tests a copy)"""
import datetime as dt, json, os, shutil, subprocess, tempfile, textwrap, unittest

TOOL = os.environ.get("VE_BIN") or os.path.expanduser("~/bin/vault-events")

FAKE_VAULT = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import json, os, sys
    fx = json.load(open(os.environ["FX"]))
    a = sys.argv[1:]
    since = a[a.index("--since") + 1]
    if since == "latest":
        print(json.dumps({"events": [], "next_cursor": fx["head"], "more": False})); sys.exit(0)
    ev = [e for e in fx["events"] if e["cursor"] > int(since)]
    print(json.dumps({"events": ev, "next_cursor": fx["head"], "more": False}))
''').lstrip()

FAKE_BILLS = "#!/bin/sh\necho \"$@\" >> \"$BILLS_CALLS\"\necho \"paperless scan since x: 1 doc(s)\"\n"


def tg(cursor, actionable=True, text="Kommst du am Samstag?"):
    return {"cursor": cursor, "source": "telegram", "ts": "2026-10-14T18:00:00+02:00", "chat_key": "123",
            "chat": "Anna", "chat_type": "private", "label": "question_to_owner" if actionable else "chatter",
            "sender": "Anna", "actionable": actionable, "priority": False, "ref": None, "text": text}


def filed(cursor, doc_id, bill=True, with_doc=True):
    e = {"cursor": cursor, "source": "paperless", "ts": "2026-10-14T18:01:00+02:00", "chat_key": "intake",
         "chat": "paperless", "chat_type": "document", "label": "filed", "sender": "", "actionable": bill,
         "priority": False, "ref": f"paperless:{doc_id}"}
    if with_doc:
        e["doc"] = {"doc_id": doc_id, "type": "Rechnung", "correspondent": "Swisscom", "bill_likely": bill}
    return e


from zoneinfo import ZoneInfo
ZRH = ZoneInfo("Europe/Zurich")


def at(day, hh, mm=0):
    """epoch of 2026-10-<day> hh:mm in Zurich"""
    return dt.datetime(2026, 10, day, hh, mm, tzinfo=ZRH).timestamp()


def iso(ts):
    return dt.datetime.fromtimestamp(ts, ZRH).isoformat(timespec="seconds")


def ha(cursor, label, data, ts, significant=None):
    sig = data.get("significant", significant if significant is not None else label in ("zone", "drive"))
    return {"cursor": cursor, "source": "homeassistant", "ts": iso(ts), "chat_key": label, "chat": "Home Assistant",
            "chat_type": "status", "label": label, "sender": "", "actionable": bool(sig), "priority": False,
            "ref": f"ha:{label}", "ha": dict(data, significant=bool(sig))}


def zone(cursor, ts, place, band=None, town=None, zone_name=None, frm=("home", None, None), **kw):
    return ha(cursor, "zone", {"place": place, "zone": zone_name if place != "home" else "Home", "band": band,
                               "town": town, "flag": "home" if place == "home" else "far" if band == "far" else "away",
                               "from": {"place": frm[0], "zone": frm[1], "band": frm[2]}, **kw}, ts)


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="vault-events-test-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(self.home)
        self.vault, self.bills = os.path.join(self.d, "vault"), os.path.join(self.d, "bills")
        for p, body in ((self.vault, FAKE_VAULT), (self.bills, FAKE_BILLS)):
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)
        self.fx, self.calls_f = os.path.join(self.d, "fx.json"), os.path.join(self.d, "bills.calls")
        self.state = os.path.join(self.home, ".local", "state", "vault-events")
        self.inbox = os.path.join(self.home, "inbox")
        self.env = dict(os.environ, HOME=self.home, VAULT_EVENTS_CLIENT=self.vault, VAULT_EVENTS_BILLS=self.bills,
                        VAULT_EVENTS_BILLS_WAIT="1", FX=self.fx, BILLS_CALLS=self.calls_f)
        self.fix([], 10)
        self.run_tool()                                   # first run: cursor at the end of the feed (10)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def fix(self, events, head):
        with open(self.fx, "w") as f:
            json.dump({"events": events, "head": head}, f)

    def run_tool(self, *args):
        r = subprocess.run([TOOL, *args], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def bills_calls(self):
        try:
            return [l.split() for l in open(self.calls_f).read().splitlines()]
        except FileNotFoundError:
            return []

    def notes(self):
        return sorted(os.listdir(self.inbox)) if os.path.isdir(self.inbox) else []

    def cursor(self):
        return int(open(os.path.join(self.state, "cursor")).read())

    def status(self):
        return json.load(open(os.path.join(self.state, "status.json")))

    def test_filed_starts_bills_scan_and_no_message_note(self):
        self.fix([filed(11, 57), tg(12, actionable=False)], 12)
        r = self.run_tool()
        self.assertEqual(self.notes(), [])                # the filed event alone never makes a message note
        since = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        self.assertEqual(self.bills_calls(), [["scan-paperless", "--cron", "--since", since, "--filed", "57"]])
        self.assertEqual(self.cursor(), 12)
        st = self.status()
        self.assertEqual((st["filed"], st["bills_scan"]), ([57], "started"))
        self.assertIn("bills scan started for paperless #57", r.stdout)
        log = os.path.join(self.state, "bills-scan.log")
        self.assertEqual(oct(os.stat(log).st_mode & 0o777), "0o600")
        self.assertIn("paperless scan since", open(log).read())

    def test_mixed_batch_note_has_no_paperless_line(self):
        self.fix([tg(11), filed(12, 58), filed(13, 59, bill=False, with_doc=False), filed(14, 58)], 14)
        self.run_tool("--cron")
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        text = open(os.path.join(self.inbox, notes[0])).read()
        self.assertIn("New messages for the owner: 1 (telegram)", text)
        self.assertNotIn("paperless", text)
        self.assertEqual(self.bills_calls()[0][-1], "58,59")   # deduped; doc id from the ref as a fallback

    def test_dated_plan_makes_a_note_even_when_not_actionable(self):
        day = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        far = (dt.date.today() + dt.timedelta(days=40)).isoformat()
        soon = dict(tg(11, actionable=False), label="plan_datetime",
                    facts=[{"kind": "event", "what": "Zug A - B", "date": day, "start": "18:17", "status": "planned"}])
        later = dict(tg(12, actionable=False), label="plan_datetime",
                     facts=[{"kind": "event", "what": "Fest", "date": far, "status": "planned"}])
        self.fix([soon, later], 12)
        self.run_tool("--cron")
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        text = open(os.path.join(self.inbox, notes[0])).read()
        self.assertIn("New messages for the owner: 1 (telegram)", text)   # only the one within 14 days
        self.assertIn("18:17 Zug A - B", text)

    def test_cancelled_or_past_plan_stays_quiet(self):
        past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        e1 = dict(tg(11, actionable=False), label="plan_datetime",
                  facts=[{"kind": "event", "what": "x", "date": past, "status": "planned"}])
        e2 = dict(tg(12, actionable=False), label="plan_datetime",
                  facts=[{"kind": "event", "what": "y", "date": dt.date.today().isoformat(), "status": "cancelled"}])
        self.fix([e1, e2], 12)
        self.run_tool("--cron")
        self.assertEqual(self.notes(), [])

    def test_no_filed_no_bills(self):
        self.fix([tg(11)], 11)
        self.run_tool("--cron")
        self.assertEqual(self.bills_calls(), [])
        self.assertNotIn("filed", self.status())

    def test_show_only_prints(self):
        self.fix([filed(11, 60)], 11)
        r = self.run_tool("--show")
        self.assertIn("would run: scan-paperless --cron --since", r.stdout)
        self.assertEqual(self.bills_calls(), [])
        self.assertEqual(self.cursor(), 10)

    def test_bills_missing_still_moves_cursor(self):
        self.env["VAULT_EVENTS_BILLS"] = os.path.join(self.d, "nope")
        self.fix([filed(11, 61)], 11)
        self.run_tool("--cron")
        self.assertEqual(self.cursor(), 11)
        self.assertTrue(self.status()["bills_scan"].startswith("failed"))


    # ------------------------------------------------------------------ Home Assistant notes (2026-10-15)
    def ha_notes(self):
        return [n for n in self.notes() if n.startswith("vault-events-ha-")]

    def ha_text(self):
        return open(os.path.join(self.inbox, self.ha_notes()[-1])).read()

    def test_ha_significant_note_coarse_and_separate(self):
        t = at(15, 14)
        self.env["VAULT_EVENTS_NOW"] = str(t + 60)
        self.fix([zone(11, t, "away", "region", "Testdorf"), ha(12, "drive", {"state": "start"}, t + 30),
                  ha(13, "activity", {"value": "walking"}, t + 40), ha(14, "bt", {"connected": ["headphones"]}, t + 50),
                  zone(15, t + 55, "away", "near", None, significant=False), tg(16)], 16)
        self.run_tool("--cron")
        self.assertEqual(len(self.ha_notes()), 1)
        self.assertEqual(len(self.notes()), 2)              # + the separate message note
        text = self.ha_text()
        self.assertIn("whereabouts changed: 2", text)
        self.assertIn("- 14:00 · away, 5-50 km from home, near Testdorf (from home)", text)
        self.assertIn("- 14:00 · drive started", text)
        for word in ("walking", "headphones", "< 5 km"):
            self.assertNotIn(word, text)
        msg = [n for n in self.notes() if n not in self.ha_notes()][0]
        self.assertNotIn("Home Assistant", open(os.path.join(self.inbox, msg)).read())
        self.assertEqual(oct(os.stat(os.path.join(self.inbox, self.ha_notes()[0])).st_mode & 0o777), "0o600")
        self.assertEqual(self.cursor(), 16)

    def test_ha_merged_at_most_every_30_min(self):
        t = at(15, 14)
        self.env["VAULT_EVENTS_NOW"] = str(t + 10)
        self.fix([ha(11, "drive", {"state": "start"}, t)], 11)
        self.run_tool("--cron")
        self.assertEqual(len(self.ha_notes()), 1)
        self.env["VAULT_EVENTS_NOW"] = str(t + 600)        # 10 min later: waits
        self.fix([ha(11, "drive", {"state": "start"}, t), ha(12, "drive", {"state": "end", "minutes": 9}, t + 590)], 12)
        r = self.run_tool()
        self.assertEqual(len(self.ha_notes()), 1)
        self.assertIn("1 HA change(s) waiting", r.stdout)
        self.env["VAULT_EVENTS_NOW"] = str(t + 1300)
        self.fix([zone(13, t + 1200, "home", frm=("away", None, "near"))], 13)
        self.run_tool("--cron")
        self.assertEqual(len(self.ha_notes()), 1)
        self.env["VAULT_EVENTS_NOW"] = str(t + 1810)       # 30 min after the first note: one merged note
        self.fix([], 13)
        self.run_tool("--cron")
        notes = self.ha_notes()
        self.assertEqual(len(notes), 2)
        text = open(os.path.join(self.inbox, notes[-1])).read()
        self.assertIn("drive ended (9 min)", text)
        self.assertIn("home (from away, < 5 km from home)", text)
        self.assertIn("whereabouts changed: 2", text)

    def test_ha_quiet_hours_then_one_summary(self):
        night = at(16, 2)
        self.env["VAULT_EVENTS_NOW"] = str(night + 60)
        self.fix([zone(11, night, "away", "far", "Fernstadt"), zone(12, night + 3600, "zone", "far", None, "Hotel",
                                                                   frm=("away", None, "far"))], 12)
        self.run_tool("--cron")
        self.assertEqual(self.ha_notes(), [])
        self.env["VAULT_EVENTS_NOW"] = str(at(16, 7, 59))
        self.fix([], 12)
        self.run_tool("--cron")
        self.assertEqual(self.ha_notes(), [])
        self.env["VAULT_EVENTS_NOW"] = str(at(16, 8, 1))
        self.run_tool("--cron")
        self.assertEqual(len(self.ha_notes()), 1)
        text = self.ha_text()
        self.assertIn("Overnight (00-08) whereabouts summary: 2 change(s)", text)
        self.assertIn("- 02:00 · away, > 50 km from home, near Fernstadt (from home)", text)
        self.assertIn("- 03:00 · zone Hotel (from away, > 50 km from home)", text)
        self.env["VAULT_EVENTS_NOW"] = str(at(16, 9))
        self.run_tool("--cron")
        self.assertEqual(len(self.ha_notes()), 1)          # nothing new: no further note

    def test_ha_switch_off_and_dedupe(self):
        os.makedirs(os.path.join(self.home, ".config", "vault-events"))
        with open(os.path.join(self.home, ".config", "vault-events", "ha"), "w") as f:
            f.write("off\n")
        t = at(15, 15)
        self.env["VAULT_EVENTS_NOW"] = str(t + 10)
        self.fix([ha(11, "drive", {"state": "start"}, t)], 11)
        self.run_tool("--cron")
        self.assertEqual(self.ha_notes(), [])
        self.assertIn("HA notes: off", self.run_tool("--status").stdout)
        os.remove(os.path.join(self.home, ".config", "vault-events", "ha"))
        self.fix([ha(12, "drive", {"state": "end", "minutes": 4}, t + 300)], 12)
        self.env["VAULT_EVENTS_NOW"] = str(t + 400)
        self.run_tool("--cron")
        self.assertEqual(len(self.ha_notes()), 1)
        self.assertIn("drive ended (4 min)", self.ha_text())
        # a re-delivered cursor never doubles an item
        pend = os.path.join(self.state, "ha-pending.json")
        with open(pend, "w") as f:
            json.dump({"items": [{"cursor": 13, "ts": t + 500, "line": "drive started"}], "last_note": t + 400}, f)
        self.fix([ha(13, "drive", {"state": "start"}, t + 500)], 13)
        self.env["VAULT_EVENTS_NOW"] = str(t + 600)
        self.run_tool("--cron")
        self.assertEqual(len(json.load(open(pend))["items"]), 1)

    def test_ha_show_previews_without_writing(self):
        t = at(15, 16)
        self.env["VAULT_EVENTS_NOW"] = str(t + 10)
        self.fix([ha(11, "drive", {"state": "start"}, t)], 11)
        r = self.run_tool("--show")
        self.assertIn("HA note (now or once due)", r.stdout)
        self.assertIn("drive started", r.stdout)
        self.assertEqual(self.ha_notes(), [])
        self.assertFalse(os.path.exists(os.path.join(self.state, "ha-pending.json")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
