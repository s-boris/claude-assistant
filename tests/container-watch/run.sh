#!/usr/bin/env bash
# Offline tests for ~/bin/container-watch. Exit 0 = all pass. Never touches the network, ssh unraid, the real
# state/cache/inbox or Telegram (fake ssh, unraid-ssh and tg in a temp dir; sockets blocked in the cron runs).
exec python3 -I "$(dirname "$0")/test_container_watch.py" "$@"
