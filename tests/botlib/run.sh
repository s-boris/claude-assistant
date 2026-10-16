#!/usr/bin/env bash
# Tests for ~/bin/lib/botlib.py. Exit 0 = all pass. Temp dirs only; fake tg/unraid-ssh scripts.
exec python3 -I "$(dirname "$0")/test_botlib.py" "$@"
