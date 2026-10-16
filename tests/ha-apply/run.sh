#!/bin/sh
# Offline tests for ha-apply (Guard allowlists). HA_APPLY_BIN=/path overrides the script under test.
# Synthetic profile (tests never read the real one): repo layout tests/profile, live layout ~/opt/profile-tests.
_pf="$(cd "$(dirname "$0")/.." && pwd)"; [ -f "$_pf/profile/fixtures/profile.json" ] && _pf=$_pf/profile || _pf=$_pf/profile-tests
export ASSISTANT_PROFILE="$_pf/fixtures/profile.json"
cd "$(dirname "$0")" && PYTHONDONTWRITEBYTECODE=1 exec python3 -m unittest test_guard test_round2 test_round3 test_scene_create "$@"
