#!/usr/bin/env bash
# Tests for bin/profile-get and bin/lib/assistant_profile.py (the owner's shared settings file).
# Offline; only the synthetic fixture and temp files. Run: bash ~/opt/profile-tests/run.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PG=${PROFILE_GET:-$(cd "$HERE/../.." && pwd)/bin/profile-get}   # repo: tests/profile -> bin/; live: ~/opt/profile-tests -> ~/bin
LIB=$(dirname "$(readlink -f "$PG")")/lib
FIX=$HERE/fixtures/profile.json
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { if eval "$1"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2"; fi; }
sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

# 1. no file: the public placeholder defaults
export ASSISTANT_PROFILE=$T/none.json
ok '[ "$("$PG" home.town)" = Exampletown ]' "no file -> default town"
ok '[ "$("$PG" network.lan_prefix)" = 198.51.100. ]' "no file -> default LAN prefix"
ok '[ "$("$PG" --source)" = defaults ]' "no file -> source defaults"
ok '"$PG" --check >/dev/null 2>&1' "no file -> --check ok"
ok '[ "$("$PG" interests.topics)" = "[]" ]' "empty list prints as []"

# 2. the fixture overrides every key
export ASSISTANT_PROFILE=$FIX
ok '[ "$("$PG" home.town)" = Testhausen ]' "fixture town"
ok '[ "$("$PG" network.hosts.web_vm)" = web@192.0.2.20 ]' "nested key"
ok '[ "$("$PG" owner.birth_year)" = 1985 ]' "number prints plain"
ok '[ "$("$PG" home.lat)" = 46.1 ]' "float prints plain"
ok '[ "$("$PG" interests.topics)" = "[\"robotics\",\"astronomy\"]" ]' "list prints as compact JSON"
ok '[ "$("$PG" --source)" = "$FIX" ]' "source is the fixture"
ok '"$PG" network | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"server_name\"]==\"Testsrv\""' "object prints as JSON"

# 3. --hash: sha256 of exactly the printed text, never the value
h=$("$PG" home.town --hash)
ok '[ "$h" = "$(sha "Testhausen")" ]' "--hash = sha256 of the value"
ok '[ "$("$PG" --hash interests.events)" = "$(sha "[\"Testfest\"]")" ]' "--hash of a list = sha256 of its JSON"
ok '! "$PG" home.town --hash | grep -q Testhausen' "--hash never prints the value"

# 4. --keys lists names only
ok '"$PG" --keys | grep -qx network.hosts.web_vm && "$PG" --keys | grep -qx home.town' "--keys lists dotted names"
ok '! "$PG" --keys | grep -q Testhausen' "--keys never prints values"

# 5. partial file: other keys keep the defaults; unknown key exit 2
printf '{"home": {"town": "Elsewhere"}}\n' > "$T/p.json"; chmod 600 "$T/p.json"
export ASSISTANT_PROFILE=$T/p.json
ok '[ "$("$PG" home.town)" = Elsewhere ] && [ "$("$PG" home.street)" = "Examplestrasse 1" ]' "partial file merges onto defaults"
"$PG" home.nope >/dev/null 2>&1; rc=$?
ok '[ $rc = 2 ]' "unknown key -> exit 2 (got $rc)"
"$PG" >/dev/null 2>&1; rc=$?
ok '[ $rc = 2 ]' "no key -> usage, exit 2 (got $rc)"

# 6. broken files: exit 1, the message names the key, never the value
printf '{"home": {"town": ' > "$T/bad.json"
export ASSISTANT_PROFILE=$T/bad.json
out=$("$PG" home.town 2>&1); rc=$?
ok '[ $rc = 1 ]' "invalid JSON -> exit 1 (got $rc)"
printf '{"home": {"postcode": 8000, "town": "Secretville"}}\n' > "$T/type.json"
export ASSISTANT_PROFILE=$T/type.json
out=$("$PG" home.town 2>&1); rc=$?
ok '[ $rc = 1 ] && echo "$out" | grep -q home.postcode && ! echo "$out" | grep -q 8000' "wrong type -> exit 1, key named, value not shown"
printf '[1, 2]\n' > "$T/list.json"
ASSISTANT_PROFILE=$T/list.json "$PG" home.town >/dev/null 2>&1; rc=$?
ok '[ $rc = 1 ]' "top level not an object -> exit 1"

# 7. --check: unknown keys (names only) and file mode; free-form hosts are fine
printf '{"home": {"twon": "Typo"}, "network": {"hosts": {"nas": "192.0.2.30"}}}\n' > "$T/c.json"; chmod 644 "$T/c.json"
export ASSISTANT_PROFILE=$T/c.json
out=$("$PG" --check 2>&1); rc=$?
ok '[ $rc = 1 ] && echo "$out" | grep -q "mode 644"' "--check: group/world-readable file -> exit 1"
chmod 600 "$T/c.json"
out=$("$PG" --check 2>&1); rc=$?
ok '[ $rc = 0 ] && echo "$out" | grep -q "unknown key home.twon" && ! echo "$out" | grep -q "hosts.nas" && ! echo "$out" | grep -q Typo' "--check: unknown key named, hosts free-form, no values"
ok '[ "$("$PG" network.hosts.nas)" = 192.0.2.30 ] && [ "$("$PG" network.hosts.web_vm)" = "user@198.51.100.3" ]' "extra host merged next to the default one"

# 8. the library: default arg, KeyError, copies, reload on change, ProfileError
export ASSISTANT_PROFILE=$T/lib.json
printf '{"home": {"town": "One"}}\n' > "$T/lib.json"
ok 'python3 -I "$HERE/test_lib.py" "$LIB" "$T/lib.json"' "library: get/default/KeyError/copies/reload/ProfileError"
ok 'python3 -I -c "import sys; sys.path.insert(0, \"$LIB\"); import profile, assistant_profile; assert hasattr(profile, \"Profile\")"' "stdlib profile module not shadowed"

echo "profile tests: $pass passed, $fail failed"
[ $fail = 0 ]
