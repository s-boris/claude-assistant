#!/usr/bin/env bash
# Offline tests for ~/bin/ai-releases. Exit 0 = all pass. Never touches the network, the real state, the LLM or Telegram.
exec python3 -I "$(dirname "$0")/test_ai_releases.py"
