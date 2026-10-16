#!/bin/sh
# Offline tests for ~/bin/pretrip-check (synthetic data, stub tools; no ssh, no network).
# Test a scratch copy first: PRETRIP_UNDER_TEST=/path/to/pretrip-check ~/opt/pretrip-check-tests/run.sh
exec python3 "$(dirname "$0")/test_pretrip.py" "$@"
