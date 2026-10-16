#!/usr/bin/env python3
"""Regression run for ~/bin/transcribe (or any command that prints a transcript).

usage:
  run.py [--name NAME] [--repeat N] [--only REGEX] [--quiet] [-- CMD ... {} ...]
      runs CMD for every case in cases.tsv ({} = audio path; default: ~/bin/transcribe {}),
      prints per-case WER and wall time, and saves NAME.json in the results dir
  run.py --table [NAME ...]   compare saved results (all if no names)

WER: word-level Levenshtein after normalising case, punctuation, hyphens, diacritics, ß->ss and small numbers.
Cases with an empty expected text (silence, noise) count every output word as a hallucination.
Time: wall clock of the whole command (ffmpeg + model load + inference), best of --repeat runs.
"""
import json, os, re, subprocess, sys, time, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
# results hold whisper's guesses at real names: private, outside the repo (cases.tsv and audio/ are private-only)
RESULTS = os.environ.get("TRANSCRIBE_BENCH_DIR") or os.path.expanduser("~/reports/transcribe-bench")
NUM = {"en": "zero one two three four five six seven eight nine ten eleven twelve".split(),
       "de": "null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf".split()}


def norm(text, lang):
    # fold diacritics: "Zurich"/"Zürich" are both fine for the owner
    t = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[-–—/]", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    words = []
    for w in t.split():
        if w.isdigit() and int(w) <= 12:
            w = NUM[lang][int(w)]
        words.append(w)
    return words


def edits(ref, hyp):
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)]


def cases():
    for line in open(os.path.join(HERE, "cases.tsv"), encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        cid, _voice, text = (line.rstrip("\n").split("\t") + [""])[:3]
        yield cid, text


def run(name, cmd, repeat, only, quiet):
    rows, tot_err, tot_ref, halluc = [], 0, 0, 0
    for cid, ref_text in cases():
        if only and not re.search(only, cid):
            continue
        audio = os.path.join(HERE, "audio", cid + ".ogg")
        argv = [a.replace("{}", audio) for a in cmd]
        best, out = None, ""
        for _ in range(repeat):
            t0 = time.monotonic()
            p = subprocess.run(argv, capture_output=True, text=True)
            dt = time.monotonic() - t0
            if p.returncode != 0:
                print(f"{cid}: command failed ({p.returncode}): {p.stderr.strip()[-300:]}", file=sys.stderr)
            out = p.stdout.strip()
            best = dt if best is None else min(best, dt)
        lang = cid.split("_")[0] if cid[:3] in ("en_", "de_") else "en"
        ref, hyp = norm(ref_text, lang), norm(out, lang)
        if ref:
            e = edits(ref, hyp)
            tot_err += e
            tot_ref += len(ref)
            wer = e / len(ref)
        else:
            e, wer = len(hyp), None
            halluc += len(hyp)
        rows.append({"id": cid, "ref": ref_text, "hyp": out, "errors": e, "ref_words": len(ref),
                     "wer": wer, "time": round(best, 2)})
        if not quiet:
            mark = "" if e == 0 else f"  <-- {out!r}"
            w = f"{wer*100:5.1f}%" if wer is not None else f"{e:3d} hw"
            print(f"{cid:20s} {w}  {best:5.2f}s{mark}", flush=True)
    summary = summarize(rows)
    print(f"== {name}: WER {summary['wer']:.2f}% ({tot_err}/{tot_ref}), hallucinated words {halluc}, "
          f"mean {summary['mean_time']:.2f}s, en_long(30s) {summary['long_time']}s, short mean {summary['short_time']}s")
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "cmd": cmd, "env": {k: v for k, v in os.environ.items() if k.startswith("TRANSCRIBE")},
                   "summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)


def summarize(rows):
    err = sum(r["errors"] for r in rows if r["ref_words"])
    ref = sum(r["ref_words"] for r in rows)
    times = [r["time"] for r in rows]
    short = [r["time"] for r in rows if r["ref_words"] and r["ref_words"] < 20]
    long_ = [r["time"] for r in rows if r["id"] == "en_long"]
    return {"wer": 100 * err / ref if ref else 0.0, "errors": err, "ref_words": ref,
            "halluc": sum(r["errors"] for r in rows if not r["ref_words"]),
            "mean_time": sum(times) / len(times) if times else 0.0,
            "short_time": round(sum(short) / len(short), 2) if short else None,
            "long_time": long_[0] if long_ else None}


def table(names):
    d = RESULTS
    files = [os.path.join(d, n + ".json") for n in names] if names else \
        sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")), key=os.path.getmtime)
    print(f"{'config':28s} {'WER':>6s} {'err':>4s} {'hallu':>5s} {'short':>6s} {'30s':>6s} {'mean':>6s}")
    for fn in files:
        rows = json.load(open(fn, encoding="utf-8"))["rows"]
        for r in rows:  # rescore with the current normalisation
            lang = r["id"].split("_")[0] if r["id"][:3] in ("en_", "de_") else "en"
            ref, hyp = norm(r["ref"], lang), norm(r["hyp"], lang)
            r["ref_words"], r["errors"] = len(ref), (edits(ref, hyp) if ref else len(hyp))
        s = summarize(rows)
        print(f"{os.path.basename(fn)[:-5]:28s} {s['wer']:5.2f}% {s['errors']:4d} {s['halluc']:5d} "
              f"{(s['short_time'] or 0):5.2f}s {(s['long_time'] or 0):5.2f}s {s['mean_time']:5.2f}s")


def main(a):
    if a and a[0] == "--table":
        return table(a[1:])
    name, repeat, only, quiet, cmd = "default", 1, None, False, None
    while a:
        x = a.pop(0)
        if x == "--name": name = a.pop(0)
        elif x == "--repeat": repeat = int(a.pop(0))
        elif x == "--only": only = a.pop(0)
        elif x == "--quiet": quiet = True
        elif x == "--": cmd = a; break
        else: sys.exit(__doc__)
    if not os.path.exists(os.path.join(HERE, "cases.tsv")):   # cases + audio are private-only (real names)
        print("transcribe bench: no cases.tsv here (private-only): nothing to run")
        return 0
    run(name, cmd or [os.path.expanduser("~/bin/transcribe"), "{}"], repeat, only, quiet)


if __name__ == "__main__":
    main(sys.argv[1:])
