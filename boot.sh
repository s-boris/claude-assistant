#!/bin/sh
# My boot-time setup: things outside /home/node are rebuilt on every container start.
# Run by /usr/local/bin/entrypoint (as node, before the main session starts). Keep it idempotent.

# telegram-plus: allow the custom channel plugin via managed settings,
# and install the launcher that prefers it, falling back to the official plugin.
ops=/home/node/plugins/telegram-plus/ops
if [ -f "$ops/managed-settings.json" ] && [ -x "$ops/start-claude" ]; then
  sudo install -D -m 644 "$ops/managed-settings.json" /etc/claude-code/managed-settings.json
  sudo install -m 755 "$ops/start-claude" /usr/local/bin/start-claude
fi
# Bot token file (2026-10-12): the entrypoint rewrites it from the docker env var at every start; keep it owner-only.
# start-claude keeps the env var itself out of the main session (tools read this file at call time).
chmod 600 /home/node/.claude/channels/telegram/.env 2>/dev/null

# Cron heartbeat fallback (2026-10-13): the entrypoint writes /etc/cron.d/heartbeat AFTER this script, with
# ~/.local/bin first on its PATH, so `heartbeat N` runs this mod-aware wrapper, which execs the image's
# /usr/local/bin/heartbeat only when the heartbeat mod is dead, overdue (nextDue + 3 min) or failing, and keeps its
# stuck-dialog alert. Keep the installed copy current. Rollback: rm ~/.local/bin/heartbeat and drop these lines.
hbw=/home/node/plugins/heartbeat/ops/heartbeat-cron
[ -f "$hbw" ] && ! cmp -s "$hbw" /home/node/.local/bin/heartbeat && install -D -m 755 "$hbw" /home/node/.local/bin/heartbeat

# OpenSSH client (for the owner's home server; key in ~/.ssh persists, the package doesn't).
command -v ssh >/dev/null || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-client; } >/dev/null 2>&1

# img2pdf (paperless skill: merge several photos of one document into a single PDF before upload).
command -v img2pdf >/dev/null || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq img2pdf; } >/dev/null 2>&1

# Voice notes (~/bin/transcribe, 2026-10-09): whisper.cpp + models live in ~/opt and persist; only the apt
# runtime deps need reinstalling. Rebuilding whisper.cpp would also need build-essential + cmake.
missing=; for p in ffmpeg libgomp1 rsync; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
[ -z "$missing" ] || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing; } >/dev/null 2>&1
# Warm whisper-server (127.0.0.1 only) so transcribe skips the model load; transcribe falls back to the CLI
# if it's down, and cron re-checks every 5 min. Backgrounded so boot isn't delayed by the ~1 s model load.
[ -x /home/node/bin/whisper-server-ctl ] && (/home/node/bin/whisper-server-ctl ensure >/dev/null 2>&1 &)

# Admin desk (~/bin/letter, ~/bin/pdfform, 2026-10-10): poppler-utils (pdftotext/pdftoppm/pdfimages: checks, grids,
# tests) and the venv in ~/opt/letter-venv. The venv persists; it's rebuilt (in the background) only if it no longer
# imports, e.g. after the image's python changed.
command -v pdftotext >/dev/null || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq poppler-utils; } >/dev/null 2>&1
# tesseract + deu/fra/ita: the vault photo OCR tests (tests/test_photos.py) run here too (2026-10-16)
command -v tesseract >/dev/null && dpkg -s tesseract-ocr-ita >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tesseract-ocr tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-ita; } >/dev/null 2>&1
lv=/home/node/opt/letter-venv
"$lv/bin/python" -c 'import reportlab, pypdf, pikepdf, PIL' 2>/dev/null || \
  ( { python3 -m venv --clear "$lv" && "$lv/bin/pip" install -q -r /home/node/opt/letter-requirements.txt; } >/dev/null 2>&1 & )

# Headed browser (~/bin/browser, 2026-10-10): Google Chrome stable on Xvfb :99 + fluxbox, CDP on 127.0.0.1:9222 for the
# Playwright MCP ("browser", user scope; pinned in ~/opt/browser-mcp, persists). Mesa libs = llvmpipe WebGL; x11vnc/noVNC
# for `browser view on`. Chrome's .deb adds Google's apt repo itself; `browser update` (cron, daily) keeps it patched.
missing=; for p in xvfb fluxbox x11-utils xdotool x11vnc novnc websockify locales libegl1 libegl-mesa0 libgl1-mesa-dri \
  libglx-mesa0 fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-color-emoji fonts-noto-cjk; do
  dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
[ -z "$missing" ] || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $missing; } >/dev/null 2>&1
if ! dpkg -s google-chrome-stable >/dev/null 2>&1; then
  d=$(mktemp -d) && curl -fsSL -o "$d/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$d/chrome.deb"; } >/dev/null 2>&1
  rm -rf "$d"
fi
locale -a 2>/dev/null | grep -q '^de_CH.utf8$' || { sudo sed -i -E 's/^# *((de_CH|en_US)\.UTF-8 UTF-8)/\1/' /etc/locale.gen && sudo locale-gen >/dev/null 2>&1; }
# Start it in the background (skipped if I stopped it on purpose with `browser stop`); cron re-checks every 5 min.
[ -x /home/node/bin/browser ] && (/home/node/bin/browser ensure >/dev/null 2>&1 &)
# flowd: live data-flow stream for the dashboard (SSE on 127.0.0.1:8790 + ssh reverse tunnel to the nginx VM); `flowd off` = kill switch, kept across reboots
[ -x /home/node/bin/flowd ] && (/home/node/bin/flowd ensure >/dev/null 2>&1 &)

# readlater (~/bin/readlater, 2026-10-10): trafilatura + yt-dlp[default] live in ~/opt/readlater-venv (persists). Rebuild the
# venv in the background only if it no longer imports (e.g. the image's Python changed). pdftotext/pdfinfo for PDFs.
# yt-dlp is upgraded weekly by cron (`readlater update`); trafilatura stays pinned.
dpkg -s poppler-utils >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq poppler-utils; } >/dev/null 2>&1
rlv=/home/node/opt/readlater-venv
"$rlv/bin/python3" -I -c 'import trafilatura, yt_dlp, yt_dlp_ejs' >/dev/null 2>&1 || ( {
  { dpkg -s python3-venv >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv; }; } &&
  python3 -m venv --clear "$rlv" && "$rlv/bin/pip" install -q --disable-pip-version-check "trafilatura==2.3.1" "yt-dlp[default]"
} >/dev/null 2>&1 & )

# Local LLM for private data (~/bin/llm-local, 2026-10-10): llama.cpp (pinned tag, built in ~/opt/llama.cpp) + Qwen3.5-9B
# Q4_K_M with MTP in ~/opt/llm-models, both persist. `install` is a no-op unless the binary is broken/missing (then it
# rebuilds, installing build-essential/cmake) or the model is missing (sha256-checked download). Then start llama-server
# on 127.0.0.1:8179 (nice 19, 8 threads) unless I stopped it on purpose; cron re-checks every 5 min. Backgrounded.
# moved to claude-vault 2026-10-10 (rollback: uncomment + rm ~/.config/llm-local/backend): [ -x /home/node/bin/llm-server-ctl ] && ( { /home/node/bin/llm-server-ctl install && /home/node/bin/llm-server-ctl ensure; } >/dev/null 2>&1 & )

# tgread (2026-10-10): runs inside the privacy vault (container claude-vault) since 2026-10-10; ~/bin/tgread is a wrapper
# around `vault tg`. The local venv is only for its offline tests (~/opt/tgread-tests/run.sh, against the canonical
# copy ~/opt/claude-vault/vendor/tgread). Telethon 1.45.0 + deps pinned with sha256 in ~/opt/tgread-*requirements.txt;
# rebuilt in the background only if the selfcheck fails (e.g. the image's Python changed).
tv=/home/node/opt/tgread-venv
"$tv/bin/python3" -I /home/node/opt/claude-vault/vendor/tgread selfcheck >/dev/null 2>&1 || ( {
  { dpkg -s python3-venv >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=300 python3-venv; }; } &&
  python3 -m venv --clear "$tv" &&
  "$tv/bin/pip" install -q --disable-pip-version-check --require-hashes --no-deps -r /home/node/opt/tgread-build-requirements.txt &&
  "$tv/bin/pip" install -q --disable-pip-version-check --require-hashes --no-deps --no-build-isolation --use-pep517 -r /home/node/opt/tgread-requirements.txt
} >/dev/null 2>&1 & )

# GitHub CLI (create repos and push to his account; login lives in ~/.config/gh)
command -v gh >/dev/null || {
  sudo mkdir -p -m 755 /etc/apt/keyrings &&
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null &&
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg &&
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null &&
  sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gh; } >/dev/null 2>&1
