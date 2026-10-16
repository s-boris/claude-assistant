"""Library checks for assistant_profile (called by run.sh with: LIB_DIR PROFILE_PATH; $ASSISTANT_PROFILE = PROFILE_PATH)."""
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import assistant_profile as p  # noqa: E402

f = sys.argv[2]
assert p.get("home.town") == "One"
assert p.get("home.nope", "dflt") == "dflt"
try:
    p.get("home.nope")
    raise SystemExit("no KeyError")
except KeyError:
    pass
d = p.load()
d["home"]["town"] = "Changed"
assert p.get("home.town") == "One", "load() must return a copy"
aliases = p.get("home.town_aliases")
aliases.append("x")
assert p.get("home.town_aliases") == [], "get() must return a copy"
with open(f, "w") as fh:
    fh.write('{"home": {"town": "Two!"}}')
later = time.time_ns() + 10**9
os.utime(f, ns=(later, later))
assert p.get("home.town") == "Two!", "a changed file must be picked up"
with open(f, "w") as fh:
    fh.write('{"home": "flat"}')
try:
    p.load()
    raise SystemExit("no ProfileError")
except p.ProfileError as e:
    assert "home" in str(e) and "flat" not in str(e)
assert all(not k.startswith("_") for k in p.DEFAULTS)
