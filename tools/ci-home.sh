#!/usr/bin/env bash
# Build the assistant container's home layout in an EMPTY directory from this checkout, for CI test runs.
#   tools/ci-home.sh HOMEDIR        then run the tests with HOME=HOMEDIR
# The scripts expect the container layout (~/bin, ~/crontab, ~/.claude/{settings.json,hooks,skills,agents},
# ~/workspace, ~/opt/<suite>-tests, ~/inbox, ~/.local/state). Nothing here is private: no secrets, no profiles;
# tests that need a real profile or config skip themselves when it is missing.
set -eu
H=${1:?usage: tools/ci-home.sh HOMEDIR}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$H"/{inbox,reports,jobs,opt,.config,.cache,.claude,.local/state/remind}
ln -sfn "$ROOT/bin" "$H/bin"
[ -e "$ROOT/bin/secretscan" ] || ln -s secretscan.py "$ROOT/bin/secretscan"     # the container's name for it
mkdir -p "$H/workspace" && cp -R "$ROOT/templates/workspace/." "$H/workspace/"
cp "$ROOT/crontab" "$H/crontab"
for d in hooks skills agents; do [ -d "$ROOT/claude/$d" ] && ln -sfn "$ROOT/claude/$d" "$H/.claude/$d"; done
[ -f "$ROOT/claude/settings.json" ] && ln -sfn "$ROOT/claude/settings.json" "$H/.claude/settings.json"
for t in "$ROOT"/tests/*/; do s=$(basename "$t"); ln -sfn "${t%/}" "$H/opt/$s-tests"; done
echo "ci-home: layout ready in $H"
