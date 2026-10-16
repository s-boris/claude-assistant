#!/usr/bin/python3 -I
"""Unit tests for ~/bin/kk-compare on a fixture (no network), plus a real-data smoke test."""
import importlib.machinery, importlib.util, json, os, re, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixture")
# Synthetic profile (tests never read the real one): repo layout tests/profile, live layout ~/opt/profile-tests.
for _pf in ('profile', 'profile-tests'):
    _pf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _pf, 'fixtures', 'profile.json')
    if os.path.isfile(_pf):
        os.environ['ASSISTANT_PROFILE'] = _pf
        break
KK = os.environ.get("KK_BIN") or os.path.expanduser("~/bin/kk-compare")   # KK_BIN: test a copy
FIX_ENV = dict(os.environ, KK_DATA_DIR=FIX, KK_OFFLINE="1", KK_PROFILE=os.path.join(FIX, "profile.json"))


def load_module(env):
    old = dict(os.environ)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_loader("kk", importlib.machinery.SourceFileLoader("kk", KK))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        os.environ.clear()
        os.environ.update(old)


kk = load_module(FIX_ENV)


def run(*args, env=FIX_ENV, ok=True):
    p = subprocess.run([KK, *args], capture_output=True, text=True, env=env, timeout=120)
    if ok and p.returncode != 0:
        raise AssertionError(f"kk-compare {args} failed: {p.stderr}")
    return p


def rank_json(*args, env=FIX_ENV):
    return json.loads(run(*args, "--json", env=env).stdout)["offers"]


class Maths(unittest.TestCase):
    def test_out_of_pocket(self):
        self.assertEqual(kk.out_of_pocket(2500, 1000, 700), 1000)
        self.assertAlmostEqual(kk.out_of_pocket(300, 1000, 700), 370)
        self.assertAlmostEqual(kk.out_of_pocket(300, 10000, 700), 1000)
        self.assertAlmostEqual(kk.out_of_pocket(2500, 100000, 700), 3200)
        self.assertEqual(kk.out_of_pocket(300, 0, 700), 0)

    def test_break_even(self):
        # premium gap 1440/yr < 0.9 x 2200: C = 300 + 1440/0.9 = 1900
        self.assertEqual(kk.break_even((540, 300), (420, 2500), 700), 1900)
        # gap 2160/yr: past the 1980 plateau, D = 1250 + 0.1 C -> C = 9100
        self.assertEqual(kk.break_even((600, 300), (420, 2500), 700), 9100)
        # gap above the 2200 maximum: the low franchise never pays off
        self.assertIsNone(kk.break_even((610, 300), (420, 2500), 700))
        self.assertEqual(kk.break_even((400, 300), (420, 2500), 700), 0.0)

    def test_classify(self):
        self.assertEqual(kk.classify("BASE", "BASE", "BASE"), "standard")
        self.assertEqual(kk.classify("PRAXIS", "HMO_Kt", "ÖKK CASAMED HMO"), "hmo")
        self.assertEqual(kk.classify("PRAXIS", "01_016", "Gesundheitspraxisversicherung"), "hmo")
        self.assertEqual(kk.classify("PRAXIS", "Santé (HMO)", "Favorit Santé"), "hmo")
        self.assertEqual(kk.classify("PRAXIS", "HAM_CBA", "ÖKK CASAMED HAUSARZT"), "hausarzt")
        self.assertEqual(kk.classify("TEL_DIG", "x", "y"), "telmed")
        self.assertEqual(kk.classify("FLEX", "x", "y"), "flex")

    def test_find_insurer(self):
        self.assertEqual(kk.find_insurer("okk"), 455)
        self.assertEqual(kk.find_insurer("ÖKK"), 455)
        self.assertEqual(kk.find_insurer("1562"), 1562)
        self.assertEqual(kk.find_insurer("helsana"), 1562)
        self.assertEqual(kk.find_insurer("Mutuel"), 1479)
        with self.assertRaises(SystemExit):
            kk.find_insurer("Groupe Mutuel")

    def test_chf(self):
        self.assertEqual(kk.chf(4242), "4'242.00")
        self.assertEqual(kk.chf(1907.4, 0), "1'907")


class Fixture(unittest.TestCase):
    def test_rank_all_models(self):
        o = rank_json("--franchise", "2500")
        got = [(x["insurer"], x["model"], x["premium"]) for x in o]
        # restricted tariff (BFS 9998/9999) is dropped for BFS 9001 (kk fixture); ZH, region 2, accident and young rows too
        self.assertEqual(got, [("KPT", "telmed", 353.5), ("ÖKK", "hmo", 357.0), ("ÖKK", "hausarzt", 365.0),
                               ("Swica", "flex", 366.0), ("Agrisano", "standard", 420.0), ("ÖKK", "standard", 421.0)])
        self.assertEqual(o[0]["year_premium"], 4242.0)
        self.assertEqual(o[0]["worst_case_year"], 4242.0 + 2500 + 700)

    def test_filters(self):
        self.assertEqual([x["premium"] for x in rank_json("--model", "standard")], [420.0, 421.0])
        self.assertEqual([x["premium"] for x in rank_json("--model", "praxis")], [357.0, 365.0])
        self.assertEqual([x["premium"] for x in rank_json("--model", "hmo")], [357.0])
        self.assertEqual([x["premium"] for x in rank_json("--accident", "yes")], [440.0, 450.0])
        self.assertEqual([x["premium"] for x in rank_json("--region", "2")], [366.9])
        self.assertEqual([x["premium"] for x in rank_json("--canton", "ZH", "--region", "1")], [400.0])
        self.assertEqual([x["premium"] for x in rank_json("--age", "young")], [380.0])
        self.assertEqual([x["premium"] for x in rank_json("--franchise", "1500", "--model", "standard")], [475.0, 480.0])
        self.assertEqual(len(rank_json("-n", "2")), 2)

    def test_restricted_area_included_for_its_bfs(self):
        prof = os.path.join(os.environ.get("TMPDIR", "/tmp"), "kk-test-profile.json")
        with open(prof, "w") as f:
            json.dump({"canton": "XX", "region": 1, "bfs": 9999, "age": "adult", "accident": "no"}, f)
        o = rank_json(env=dict(FIX_ENV, KK_PROFILE=prof))
        os.unlink(prof)
        self.assertEqual((o[0]["insurer"], o[0]["model"], o[0]["premium"]), ("CSS", "hmo", 300.0))

    def test_delta_vs_current(self):
        o = rank_json("--current-premium", "450")
        self.assertEqual(o[0]["delta_year_vs_current"], round(12 * (353.5 - 450), 2))
        txt = run("--current-premium", "450", "--current-insurer", "ÖKK").stdout
        self.assertIn("ÖKK ←", txt)
        self.assertIn("1'158 CHF/year less with KPT", txt)

    def test_matrix(self):
        cells = json.loads(run("--franchise-matrix", "--json").stdout)["cells"]
        c = {(x["model"], x["franchise"]): x for x in cells}
        self.assertEqual((c[("standard", 300)]["insurer"], c[("standard", 300)]["premium"]), ("ÖKK", 538.0))
        self.assertEqual((c[("standard", 2500)]["insurer"], c[("standard", 2500)]["premium"]), ("Agrisano", 420.0))
        # 300 + 12 x (538 - 420) / 0.9 = 1873.3
        self.assertEqual(c[("standard", 300)]["break_even_vs_top"], 1873)
        self.assertNotIn(("telmed", 1500), c)
        self.assertIsNone(c[("standard", 2500)]["break_even_vs_top"])
        txt = run("--franchise-matrix").stdout
        self.assertIn("1'873", txt)

    def test_costs(self):
        rows = json.loads(run("--costs", "--json", "--medical", "0,5000", "--model", "standard").stdout)["rows"]
        self.assertEqual(rows[0]["best"]["standard"]["total"], 5040.0)
        self.assertEqual(rows[0]["best"]["standard"]["franchise"], 2500)
        # 12 x 538 + 300 + 470 = 7226 (ÖKK 300) beats Agrisano 1500 (7610) and 2500 (7790)
        self.assertEqual(rows[1]["best"]["standard"]["total"], 7226.0)
        self.assertEqual(rows[1]["best"]["standard"]["insurer"], "ÖKK")
        rows = json.loads(run("--costs", "--json", "--medical", "1000", "--current-premium", "450",
                              "--current-franchise", "300").stdout)["rows"]
        self.assertEqual(rows[0]["current"], 12 * 450 + 300 + 70)

    def test_letter(self):
        t = run("--letter", "--insurer", "ÖKK", "--policy", "123.456", "--year", "2026").stdout
        for s in ("Kündigung der obligatorischen Krankenpflegeversicherung", "per 31. Dezember 2026",
                  "ab 1. Januar 2027", "EINSCHREIBEN", "ÖKK Kranken- und Unfallversicherungen AG",
                  "7302 Landquart", "Versicherten-Nr.: 123.456", "Robin Tester", "30. November 2026"):
            self.assertIn(s, t)
        t = run("--letter", "--year", "2026").stdout
        self.assertIn("[Name der Krankenkasse]", t)
        self.assertIn("[Versichertennummer", t)

    def test_sender_from_profile(self):
        # the kk fixture has no name/street/plz_city/city (as the live kk profile): the assistant profile fills them
        t = run("--letter", "--insurer", "ÖKK", "--year", "2026").stdout
        for s in ("Robin Tester", "Teststrasse 9", "4321 Testhausen", "Testhausen, ", "01.01.1990"):
            self.assertIn(s, t)
        tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "kk-test-sender.json")
        try:
            # the kk profile still wins
            with open(tmp, "w") as f:
                json.dump({"name": "Kim Muster", "street": "Kiesweg 3", "plz_city": "9999 Kieswil", "city": "Kieswil",
                           "canton": "XX", "region": 1, "bfs": 9001}, f)
            t = run("--letter", "--year", "2026", env=dict(FIX_ENV, KK_PROFILE=tmp)).stdout
            for s in ("Kim Muster", "Kiesweg 3", "9999 Kieswil"):
                self.assertIn(s, t)
            self.assertNotIn("Robin Tester", t)
            # no profile file: placeholders, never the public defaults
            t = run("--letter", "--year", "2026", env=dict(FIX_ENV, ASSISTANT_PROFILE=tmp + ".missing")).stdout
            for s in ("[Vorname Name]", "[Strasse Nr.]", "[PLZ Ort]"):
                self.assertIn(s, t)
            self.assertNotIn("Alex Doe", t)
            # canton from the assistant profile when the kk profile has none (fixture canton ZZ has no rows)
            with open(tmp, "w") as f:
                json.dump({"region": 1, "age": "adult", "accident": "no"}, f)
            p = run("--json", env=dict(FIX_ENV, KK_PROFILE=tmp), ok=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("canton ZZ", p.stderr)
            p = run("--json", env=dict(FIX_ENV, KK_PROFILE=tmp, ASSISTANT_PROFILE=tmp + ".missing"), ok=False)
            self.assertIn("no canton", p.stderr)
        finally:
            os.unlink(tmp)

    def test_bad_input(self):
        self.assertNotEqual(run("--franchise", "1234", ok=False).returncode, 0)
        self.assertNotEqual(run("--model", "nope", ok=False).returncode, 0)
        self.assertNotEqual(run("--region", "7", ok=False).returncode, 0)


REAL = os.path.expanduser("~/.cache/kk/praemien_ch.csv")
REAL_ENV = dict(os.environ, KK_OFFLINE="1", KK_PROFILE=os.path.join(FIX, "profile.json"))


@unittest.skipUnless(os.path.exists(REAL), "no cached BAG data")
class RealData(unittest.TestCase):
    """Smoke tests on the cached BAG data, canton-independent (public facts only)."""

    def test_standard_range(self):
        o = rank_json("--canton", "BE", "--region", "1", "--age", "adult", "--franchise", "2500",
                      "--accident", "no", "--model", "standard", "-n", "99", env=REAL_ENV)
        p = [x["premium"] for x in o]
        self.assertGreater(len(o), 5)
        self.assertEqual(p, sorted(p))
        self.assertTrue(all(200 < x < 1000 for x in p), p)

    def test_acceptance_command(self):
        t = run("--canton", "BE", "--region", "1", "--age", "adult", "--franchise", "2500", "--accident", "no",
                "-n", "5", env=REAL_ENV).stdout
        lines = [l for l in t.splitlines() if re.match(r"^\s?\d{1,2}  \S", l)]
        self.assertEqual(len(lines), 5)

    def test_region_of_bern(self):
        t = run("--region-of", "3011", env=REAL_ENV).stdout
        self.assertIn("Bern (BFS 351), canton BE, premium region 1", t)

    def test_all_insurers_named(self):
        m = load_module(dict(REAL_ENV))
        for canton in ("BE", "ZH", "GE"):
            offers, _ = m.load_offers(canton)
            self.assertTrue(offers)
            self.assertFalse({o.ins for o in offers} - set(kk.INSURERS), canton)


if __name__ == "__main__":
    unittest.main(verbosity=1)
