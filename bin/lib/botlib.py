"""botlib: the few helpers that several ~/bin scripts share word for word. Keep it small and boring.
Import:  sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib")); import botlib
Users: container-watch, hwtrend, media, unraid-capacity, unraid-health, watch. Tests: ~/opt/botlib-tests/run.sh."""
import json
import os
import subprocess
import time
from pathlib import Path


def atomic_write(path, text):
    """Write text through a hidden temp file + rename (readers never see half a file); creates the parent dir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_json(path, default=None):
    """Parsed JSON file, or default when it is missing or unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj, **dump_kw):
    atomic_write(path, json.dumps(obj, **dump_kw))


def write_inbox(inbox_dir, prefix, text):
    """New note <inbox_dir>/<prefix>-<epoch>[-n].md (never overwrites one); returns its Path."""
    t, n = int(time.time()), 0
    f = Path(inbox_dir) / f"{prefix}-{t}.md"
    while f.exists():
        n += 1
        f = Path(inbox_dir) / f"{prefix}-{t}-{n}.md"
    atomic_write(f, text)
    return f


def send_tg(cmd, text, timeout=60):
    """Text the owner through the tg sender `cmd` -> (ok, stderr/stdout or error text; callers shorten it)."""
    try:
        p = subprocess.run([cmd, text], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stderr or p.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def lockdown_mode(unraid_ssh):
    """root: a full-root key -> the direct `bash -s` + COLLECTOR path. verbs/emulate: the owner's SSH
    lockdown (~/opt/unraid-lockdown): the same collector runs server-side as one allowlisted verb.
    ~/bin/unraid-ssh (`unraid_ssh`) decides (auto-detects the forced command)."""
    try:
        m = subprocess.run([unraid_ssh, "mode"], capture_output=True, text=True, timeout=90).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "root"
    return m if m in ("verbs", "emulate") else "root"
