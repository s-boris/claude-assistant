"""assistant_profile: the owner's shared personal settings, read from one private file.

Code that needs a personal value (the owner's name, home town, server name, LAN prefix, ...) reads it here
instead of hard-coding it. The file is ~/.config/assistant/profile.json (mode 0600, never exported or
published); $ASSISTANT_PROFILE points somewhere else (tests point it at a synthetic fixture profile).
Every key has a public placeholder default (DEFAULTS below), so the code runs without the file and a
published copy behaves exactly as it was reviewed. Keys in the file override the defaults one leaf at a time.

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
    import assistant_profile as profile
    profile.get("home.town")              # "Exampletown" unless the profile says otherwise
    profile.get("network.hosts.web_vm")   # dotted keys reach into sections
    profile.load()                        # the whole merged dict (a copy)
    profile.source()                      # path of the file in use, or None (defaults only)

A missing file means defaults. A file that exists but can't be read or parsed, or holds a value of the
wrong type, raises ProfileError (the message names the key, never the value): a broken profile must not
silently turn into placeholders. Callers that must keep running (hooks) catch it and use stricter
fallbacks. Shell scripts use bin/profile-get. The module is named assistant_profile, not profile, so it
never shadows Python's own `profile` module.
"""
import copy
import json
import os

DEFAULTS = {
    "owner": {
        "first_name": "Alex",
        "last_name": "Doe",
        "email": "owner@example.com",
        "phone": "+41 00 000 00 00",
        "birth_year": 1990,
        "github": "s-boris",
    },
    "home": {
        "street": "Examplestrasse 1",
        "postcode": "1234",
        "town": "Exampletown",
        "town_aliases": [],
        "canton": "XX",
        "municipality_bfs": 9001,
        "lat": 47.5,
        "lon": 8.5,
    },
    "work": {
        "town": "Worktown",
        "lat": 47.3769,
        "lon": 8.5417,
    },
    "locale": {
        "tz": "Europe/Zurich",
        "lang": "en",
        "country": "CH",
    },
    "network": {
        "server_name": "Homesrv",
        "server_host": "198.51.100.5",
        "lan_prefix": "198.51.100.",
        "domain": "example.net",
        "hosts": {
            "web_vm": "user@198.51.100.3",
        },
    },
    "services": {
        "seerr_user": "Alex",
        "ha_user": "Alex",
        "ha_url": "http://198.51.100.7:8123",    # Home Assistant base URL (bin/ha, ha-audit, ha-apply, ...)
        "ha_phone": "phone",                     # HA device slug of the owner's current phone (entity prefix)
        "ha_keep_models": [],                    # phone model codes a cleanup must never touch (his, household)
    },
    "repos": {
        "private_code": "s-boris/claude-assistant",
        "private_vault": "s-boris/claude-privacy-vault",
        "public_code": "s-boris/claude-assistant",
        "public_vault": "s-boris/claude-privacy-vault",
    },
    "interests": {
        "topics": [],
        "events": [],
    },
}

ENV = "ASSISTANT_PROFILE"
_cache = {}


class ProfileError(Exception):
    pass


def path():
    """The profile file to read: $ASSISTANT_PROFILE, else ~/.config/assistant/profile.json."""
    return os.environ.get(ENV) or os.path.join(os.path.expanduser("~"), ".config/assistant/profile.json")


def _kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return "null" if v is None else type(v).__name__


def _merge(base, over, prefix=""):
    """Deep-merge `over` into a copy of `base`; a leaf must keep the default's type."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        key = prefix + k
        if k.startswith("_"):                       # "_comment" and friends
            continue
        if k in out and isinstance(out[k], dict):
            if not isinstance(v, dict):
                raise ProfileError("profile key %s: expected an object, got %s" % (key, _kind(v)))
            out[k] = _merge(out[k], v, key + ".")
        elif k in out and _kind(out[k]) != _kind(v):
            raise ProfileError("profile key %s: expected %s, got %s" % (key, _kind(out[k]), _kind(v)))
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read():
    p = path()
    try:
        st = os.stat(p)
    except FileNotFoundError:
        return None, None, copy.deepcopy(DEFAULTS)
    except OSError as e:
        raise ProfileError("profile %s unreadable (%s)" % (p, e.__class__.__name__))
    key = (p, st.st_mtime_ns, st.st_size)
    if _cache.get("key") == key:
        return p, key, _cache["data"]
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise ProfileError("profile %s unreadable or not JSON (%s)" % (p, e.__class__.__name__))
    if not isinstance(raw, dict):
        raise ProfileError("profile %s: top level must be an object" % p)
    data = _merge(DEFAULTS, raw)
    _cache.update(key=key, data=data)
    return p, key, data


def load():
    """The merged profile (defaults + file) as a new dict."""
    return copy.deepcopy(_read()[2])


def source():
    """Path of the profile file in use, or None when only the defaults apply."""
    return _read()[0]


_MISSING = object()


def get(key, default=_MISSING):
    """Value at a dotted key. Unknown key: `default` if given, else KeyError."""
    cur = _read()[2]
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif default is not _MISSING:
            return default
        else:
            raise KeyError(key)
    return copy.deepcopy(cur)


def as_text(v):
    """How profile-get prints a value: strings as they are, everything else as compact JSON."""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def keys(d=None, prefix=""):
    """Every leaf key (dotted), sorted."""
    d = _read()[2] if d is None else d
    out = []
    for k, v in d.items():
        if isinstance(v, dict) and v:
            out += keys(v, prefix + k + ".")
        else:
            out.append(prefix + k)
    return sorted(out)


def unknown_keys():
    """Leaf keys present in the file but not in DEFAULTS (allowed, but often a typo)."""
    known = set(keys(DEFAULTS))
    known_objs = {k for k in _obj_keys(DEFAULTS)}
    return [k for k in keys() if k not in known and not any(k.startswith(o + ".") for o in known_objs)]


def _obj_keys(d, prefix=""):
    """Dotted keys whose default is an object that may hold free-form entries (e.g. network.hosts)."""
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            if k == "hosts":
                out.append(prefix + k)
            out += _obj_keys(v, prefix + k + ".")
    return out
