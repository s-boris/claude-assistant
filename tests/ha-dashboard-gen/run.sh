#!/bin/sh
# Offline tests for ha-dashboard-gen (build step only; no HA). GEN_BIN=/path tests a copy.
cd "$(dirname "$0")" && PYTHONDONTWRITEBYTECODE=1 exec python3 -m unittest test_dashboard_gen "$@"
