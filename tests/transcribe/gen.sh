#!/usr/bin/env bash
# Regenerate the test audio from cases.tsv: TTS (piper or espeak-ng) -> light pink noise (~25 dB SNR)
# -> OGG/Opus 32 kbit/s mono 48 kHz, like a Telegram voice note.
set -euo pipefail
cd "$(dirname "$0")"
PIPER=$HOME/opt/piper-venv/bin/piper; VOICES=$HOME/opt/piper-voices
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
grep -v '^#' cases.tsv | while IFS=$'\t' read -r id voice text; do
  [ -n "$id" ] || continue
  case "$voice" in
    silence) ffmpeg -nostdin -loglevel error -y -f lavfi -i "anoisesrc=c=pink:a=0.002:d=6" "$tmp/raw.wav" ;;
    noise)   ffmpeg -nostdin -loglevel error -y -f lavfi -i "anoisesrc=c=brown:a=0.15:d=8" "$tmp/raw.wav" ;;
    espeak:*) espeak-ng -v "${voice#espeak:}" -s 165 -w "$tmp/raw.wav" "$text" ;;
    *) printf '%s\n' "$text" | "$PIPER" -m "$VOICES/$voice.onnx" -f "$tmp/raw.wav" >/dev/null 2>&1 ;;
  esac
  # 0.6 s lead-in/out like a real recording, plus a quiet pink noise floor
  ffmpeg -nostdin -loglevel error -y -i "$tmp/raw.wav" -f lavfi -i "anoisesrc=c=pink:a=0.01" \
    -filter_complex "[0:a]aresample=48000,aformat=channel_layouts=mono,adelay=600,apad=pad_dur=0.6[v];[1:a]aresample=48000,aformat=channel_layouts=mono[n];[v][n]amix=inputs=2:duration=first:normalize=0" \
    -c:a libopus -b:a 32k -ac 1 -ar 48000 -application voip "audio/$id.ogg"
  printf '%-20s %5.1fs\n' "$id" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "audio/$id.ogg")"
done
