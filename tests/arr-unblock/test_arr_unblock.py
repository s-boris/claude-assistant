#!/usr/bin/env python3
"""Offline tests for ~/bin/arr-unblock: the match logic (Radarr title/year/id, Sonarr series/episodes, samples,
rejections, quality, size) on fixture JSON, and the cron flow (import, silent success, one note per skip,
failure note, API-failure threshold) against a fake API. fixtures/radarr_movie.json is a synthetic queue item,
movie, manual-import preview and parse result for "Iron Tide (2015)" whose file name parses as another title;
fixtures/sonarr_pack.json is a synthetic two-episode download with a sample. All titles and ids are invented.
Run: python3 ~/opt/arr-unblock-tests/test_arr_unblock.py"""
import atexit, copy, glob, importlib.machinery, importlib.util, json, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_ldr = importlib.machinery.SourceFileLoader("arr_unblock", "/home/node/bin/arr-unblock")
_spec = importlib.util.spec_from_loader("arr_unblock", _ldr)
AU = importlib.util.module_from_spec(_spec)
sys.dont_write_bytecode = True
_ldr.exec_module(AU)

RAD = json.loads((HERE / "fixtures/radarr_movie.json").read_text())
SON = json.loads((HERE / "fixtures/sonarr_pack.json").read_text())
MAIN_FILE = next(iter(RAD["parse"]))
P = F = 0


def ok(name, cond, got=""):
    global P, F
    if cond:
        P += 1; print(f"ok   - {name}")
    else:
        F += 1; print(f"FAIL - {name}\n       got: {str(got)[:700]}")


def rparse(title=None, year=2015, mid=2238, name=MAIN_FILE):
    """Radarr parse result for the fixture file with the given parsed title/year/movie id."""
    p = copy.deepcopy(RAD["parse"][name])
    if title is not None:
        p["parsedMovieInfo"]["movieTitles"] = [title]
        p["parsedMovieInfo"]["movieTitle"] = title
    p["parsedMovieInfo"]["year"] = year
    p["movie"] = {"id": mid} if mid else None
    return p


def radarr(parse=None, preview=None, approved=False, queue=None, movie=None):
    parses = {MAIN_FILE: parse if parse is not None else RAD["parse"][MAIN_FILE]}
    return AU.verify_radarr(queue or copy.deepcopy(RAD["queue"]), movie or copy.deepcopy(RAD["movie"]),
                            preview if preview is not None else copy.deepcopy(RAD["preview"]),
                            lambda n: parses[n] if n in parses else parses_extra.get(n),
                            approved)


parses_extra = {}


def sonarr(parse=None, preview=None, queue=None, series=None, approved=False):
    parses = copy.deepcopy(SON["parse"])
    parses.update(parse or {})
    return AU.verify_sonarr(queue or copy.deepcopy(SON["queue"]), series or copy.deepcopy(SON["series"]),
                            preview if preview is not None else copy.deepcopy(SON["preview"]),
                            lambda n: parses.get(n), approved)


def why(d):
    return " | ".join(d.problems)


# ---------- normalisation + candidates ----------
ok("norm folds case, punctuation, articles, accents, trailing year",
   AU.norm("The Iron Tide: Part 1 (2015)") == AU.norm("iron.tide part-1")
   and AU.norm("Zoé & Co") == AU.norm("Zoe and Co"), AU.norm("The Iron Tide: Part 1 (2015)"))
ok("norm keeps distinguishing words", AU.norm("Iron Tide Live Movie 1") != AU.norm("Iron Tide"))
q = RAD["queue"]
ok("fixture queue item is a candidate", AU.is_candidate(q))
ok("downloading item is not a candidate", not AU.is_candidate(dict(q, status="downloading", sizeleft=5)))
ok("importPending without manual-import message is not a candidate",
   not AU.is_candidate(dict(q, trackedDownloadState="importPending", statusMessages=[])))
ok("importPending with a Manual Import message is a candidate",
   AU.is_candidate(dict(q, trackedDownloadState="importPending")))
g = AU.group_candidates(SON["queue"] + [dict(SON["queue"][0], id=9, downloadId="other", status="downloading")])
ok("Sonarr records group by download id, non-candidates dropped", list(g) == ["SABnzbd_nzo_test1"]
   and len(g["SABnzbd_nzo_test1"]) == 2, g.keys())

# ---------- Radarr ----------
d = radarr()
ok("Iron Tide: 'Iron Tide Live Movie 1' is a title mismatch -> skip", not d.ok and not d.files
   and "Iron Tide Live Movie 1" in why(d) and "no movie" in why(d), why(d))
ok("Iron Tide: only name problems (so --approve is offered)", set(d.problems) <= set(d.naming) and d.problems)
ok("Iron Tide: skip note offers the approve command", any("--approve radarr:123456789" in l for l in AU.skip_lines(d)),
   AU.skip_lines(d))
d = radarr(approved=True)
f0 = d.files[0] if d.files else {}
ok("Iron Tide approved: structure passes, import payload complete", d.ok and f0.get("movieId") == 2238
   and f0.get("downloadId") == RAD["queue"]["downloadId"] and f0["quality"]["quality"]["name"] == "Bluray-1080p"
   and [l["name"] for l in f0["languages"]] == ["German", "Japanese"] and f0.get("releaseGroup") == "GRPD"
   and f0.get("path", "").endswith(".mkv"), (why(d), f0))

d = radarr(parse=rparse("Iron Tide"))
ok("match: exact title + year + parser id -> import", d.ok and len(d.files) == 1, why(d))
d = radarr(parse=rparse("Iron Tide Part 1"))
ok("match via alternate title ('Iron Tide: Part 1')", d.ok, why(d))
d = radarr(parse=rparse("Iron Tide Ashfall", mid=None))
ok("title mismatch (part 2 of the same year) -> skip", not d.ok and "Ashfall" in why(d), why(d))
d = radarr(parse=rparse("Iron Tide", mid=4711))
ok("parser maps the name to another movie -> skip", not d.ok and "movie 4711" in why(d), why(d))
d = radarr(parse=rparse("Iron Tide", year=2013))
ok("year mismatch (2013 vs 2015) -> skip", not d.ok and "year 2013" in why(d), why(d))
d = radarr(parse=rparse("Iron Tide", year=2016))
ok("year +-1 allowed when the title matches exactly", d.ok, why(d))
d = radarr(parse=rparse("Iron Tide Part 1", year=2016))
ok("year +-1 NOT allowed on an alternate-title match", not d.ok and "year 2016" in why(d), why(d))
d = radarr(parse=rparse("Iron Tide", year=0))
ok("no year in the name -> skip", not d.ok and "no year" in why(d), why(d))

pv = copy.deepcopy(RAD["preview"])
smp = dict(pv[0], relativePath="Sample/tide-sample.mkv", path="/x/Sample/tide-sample.mkv", size=60 * AU.MB,
           rejections=[{"reason": "Sample", "type": "permanent"}])
d = radarr(parse=rparse("Iron Tide"), preview=[smp])
ok("sample only -> skip (samples are never imported)", not d.ok and "no importable video" in why(d), why(d))
d = radarr(parse=rparse("Iron Tide"), preview=[smp, pv[0]])
ok("sample + main file -> import the main file only", d.ok and len(d.files) == 1
   and not d.files[0]["path"].endswith("sample.mkv") and any("sample left out" in n for n in d.notes), why(d))
smp2 = dict(pv[0], relativePath="tide.sample.mkv", rejections=[])
d = radarr(parse=rparse("Iron Tide"), preview=[smp2], approved=True)
ok("file named *.sample.* is a sample even without a rejection", not d.ok, why(d))
bad = dict(pv[0], rejections=[{"reason": "Not an upgrade for existing movie file(s)", "type": "permanent"}])
d = radarr(parse=rparse("Iron Tide"), preview=[bad], approved=True)
ok("other rejection -> skip, even with owner approval", not d.ok and "Not an upgrade" in why(d), why(d))
byid = dict(pv[0], rejections=[{"reason": "Found matching movie via grab history, but release was matched to "
                                          "movie by ID. Manual Import required.", "type": "permanent"}])
d = radarr(parse=rparse("Iron Tide"), preview=[byid])
ok("the by-ID rejection itself is tolerated", d.ok, why(d))
unk = dict(pv[0], quality={"quality": {"id": 0, "name": "Unknown"}, "revision": {"version": 1}})
d = radarr(parse=rparse("Iron Tide"), preview=[unk])
ok("unknown quality -> skip", not d.ok and "quality" in why(d), why(d))
tiny = dict(pv[0], size=120 * AU.MB)
d = radarr(parse=rparse("Iron Tide"), preview=[tiny])
ok("tiny file -> skip (size)", not d.ok and "size" in why(d), why(d))
part = dict(pv[0], size=2 * AU.GB)
d = radarr(parse=rparse("Iron Tide"), preview=[part])
ok("file far smaller than the download -> skip", not d.ok and "only 2.0 GB" in why(d), why(d))
other = dict(pv[0], movie={"id": 99})
d = radarr(parse=rparse("Iron Tide"), preview=[other], approved=True)
ok("preview assigns a different movie -> skip, even approved", not d.ok and "movie 99" in why(d), why(d))
two = dict(pv[0], relativePath="Extras.mkv", path="/x/Extras.mkv")
parses_extra["Extras.mkv"] = rparse("Iron Tide", name=MAIN_FILE)
d = radarr(parse=rparse("Iron Tide"), preview=[pv[0], two])
ok("two main video files -> skip", not d.ok and "2 main video files" in why(d), why(d))
d = radarr(queue=dict(RAD["queue"], movieId=None, movie=None), movie={})
ok("unknown queue item (no movie) -> skip", not d.ok and "not matched" in why(d), why(d))

# ---------- Sonarr ----------
d = sonarr()
ok("Sonarr match: both episodes import, sample left out", d.ok and len(d.files) == 2
   and sorted(sum((f["episodeIds"] for f in d.files), [])) == [19, 20]
   and all(f["seriesId"] == 1 and f["downloadId"] == "SABnzbd_nzo_test1" for f in d.files)
   and any("sample" in n for n in d.notes), (why(d), d.files))
E3 = "Seven.Clocks.S01E03.1080p.WEB.h264-GRP.mkv"
wrong = copy.deepcopy(SON["parse"][E3])
wrong["parsedEpisodeInfo"]["episodeNumbers"] = [4]
wrong["episodes"] = [{"id": 21, "seasonNumber": 1, "episodeNumber": 4}]
d = sonarr(parse={E3: wrong})
ok("wrong episode: name says S01E04, preview assigns S01E03 -> skip", not d.ok and "S01E04" in why(d), why(d))
pv = copy.deepcopy(SON["preview"])
pv[1]["episodes"] = [{"id": 21, "seasonNumber": 1, "episodeNumber": 4}]
d = sonarr(preview=pv, parse={E3: wrong})
ok("episode not part of this download -> skip", not d.ok and "not an episode of this download" in why(d), why(d))
sm = copy.deepcopy(SON["parse"][E3])
sm["parsedEpisodeInfo"]["seriesTitle"] = "Seven Clocks Revisited"
sm["parsedEpisodeInfo"]["seriesTitleInfo"] = {"title": "Seven Clocks Revisited", "titleWithoutYear": "Seven Clocks Revisited"}
sm["series"] = None
d = sonarr(parse={E3: sm})
ok("series title mismatch -> skip", not d.ok and "Seven Clocks Revisited" in why(d) and "no series" in why(d), why(d))
ym = copy.deepcopy(SON["parse"][E3])
ym["parsedEpisodeInfo"]["seriesTitleInfo"]["year"] = 1995
d = sonarr(parse={E3: ym})
ok("series year mismatch -> skip", not d.ok and "year 1995" in why(d), why(d))
alt = copy.deepcopy(SON["parse"][E3])
alt["parsedEpisodeInfo"]["seriesTitle"] = "Siete Relojes"
d = sonarr(parse={E3: alt})
ok("series alternate title matches", d.ok, why(d))
pv = copy.deepcopy(SON["preview"])
pv[1]["episodes"] = pv[0]["episodes"]
d = sonarr(preview=pv, parse={E3: SON["parse"]["Seven.Clocks.S01E02.1080p.WEB.h264-GRP.mkv"]})
ok("two files for one episode -> skip", not d.ok and "also in" in why(d), why(d))
d = sonarr(preview=[SON["preview"][0], SON["preview"][2]])
ok("pack missing an episode: import what verifies, note the gap", d.ok and len(d.files) == 1
   and any("no file for S01E03" in n for n in d.notes), (why(d), d.notes))
d = sonarr(preview=[SON["preview"][2]])
ok("Sonarr sample only -> skip", not d.ok and "no importable video" in why(d), why(d))


# ---------- cron flow against a fake API ----------
class Fake:
    def __init__(self, parse, has_file=True, cmd_status="completed", fail=False):
        self.parse, self.has_file, self.cmd_status, self.fail = parse, has_file, cmd_status, fail
        self.posts, self.imported = [], False

    def __call__(self, svc, method, path, body=None, timeout=None):
        if self.fail:
            raise AU.ApiError("cannot reach radarr: refused")
        base = path.split("?")[0]
        if base == "/api/v3/queue":
            recs = [RAD["queue"]] if svc == "radarr" and not self.imported else []
            return {"records": copy.deepcopy(recs), "totalRecords": len(recs)}
        if base == "/api/v3/manualimport":
            return copy.deepcopy(RAD["preview"])
        if base == "/api/v3/parse":
            return copy.deepcopy(self.parse)
        if base.startswith("/api/v3/movie/"):
            return dict(RAD["movie"], hasFile=self.imported and self.has_file)
        if base == "/api/v3/command" and method == "POST":
            self.posts.append(body)
            self.imported = self.cmd_status == "completed"
            return {"id": 77, "status": "queued"}
        if base == "/api/v3/command/77":
            return {"id": 77, "status": self.cmd_status, "message": "boom" if self.cmd_status == "failed" else ""}
        raise AssertionError(f"unexpected call {method} {path}")


def fresh_env():
    tmp = tempfile.mkdtemp(prefix="arr-unblock-test-")
    atexit.register(shutil.rmtree, tmp, True)
    AU.STATE_DIR, AU.LOG, AU.INBOX = f"{tmp}/state", f"{tmp}/arr-unblock.log", f"{tmp}/inbox"
    AU.time.sleep = lambda s: None
    return tmp


def notes(tmp):
    return sorted(glob.glob(f"{tmp}/inbox/arr-unblock-*.md"))


def logtxt(tmp):
    return Path(f"{tmp}/arr-unblock.log").read_text() if os.path.exists(f"{tmp}/arr-unblock.log") else ""


tmp = fresh_env()
AU.api = Fake(RAD["parse"][MAIN_FILE])
AU.run("cron")
n1 = notes(tmp)
txt = Path(n1[0]).read_text() if n1 else ""
AU.run("cron")
ok("cron, Iron Tide: one note naming the mismatch + approve command, no import",
   len(n1) == 1 and "Live Movie 1" in txt and "--approve radarr:123456789" in txt and not AU.api.posts, txt)
ok("cron, same skip again: no second note", len(notes(tmp)) == 1, notes(tmp))

tmp = fresh_env()
AU.api = Fake(rparse("Iron Tide"))
AU.run("dry")
ok("dry run never posts a command", not AU.api.posts)
AU.run("cron")
body = AU.api.posts[0] if AU.api.posts else {}
ok("cron, verified item: ManualImport posted with importMode auto and the preview's file",
   body.get("name") == "ManualImport" and body.get("importMode") == "auto" and body["files"][0]["movieId"] == 2238
   and body["files"][0]["downloadId"] == RAD["queue"]["downloadId"], body)
ok("cron success: silent (no note), logged as OK", not notes(tmp) and "OK radarr:" in logtxt(tmp)
   and "queue item gone" in logtxt(tmp), logtxt(tmp))

tmp = fresh_env()
AU.api = Fake(rparse("Iron Tide"), cmd_status="failed")
AU.run("cron")
ok("cron, import command fails: one failure note", len(notes(tmp)) == 1
   and "import failed" in Path(notes(tmp)[0]).read_text(), logtxt(tmp))

tmp = fresh_env()
AU.api = Fake(rparse("Iron Tide"), has_file=False)
AU.run("cron")
ok("cron, command ok but no file in the library: failure note", len(notes(tmp)) == 1
   and "no file" in Path(notes(tmp)[0]).read_text(), logtxt(tmp))

tmp = fresh_env()
AU.api = Fake(None, fail=True)
for i in range(3):
    AU.run("cron")
ok("API down 3 runs: still silent", not notes(tmp))
AU.run("cron")
ok("API down 4 runs: one note", len(notes(tmp)) == 1)
AU.run("cron")
ok("API down 5 runs: no repeat", len(notes(tmp)) == 1)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
