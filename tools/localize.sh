#!/usr/bin/env bash
# Replace this repository's documentation placeholders with your own values (see examples/site.env.example).
# usage: tools/localize.sh path/to/site.env
set -eu
[ -f "${1:-}" ] || { echo "usage: $0 path/to/site.env" >&2; exit 2; }
. "$1"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mapfile -t files < <(cd "$ROOT" && git ls-files bin claude templates examples dash boot.sh crontab)
sub() { [ -n "$2" ] && [ "$1" != "$2" ] || return 0
        (cd "$ROOT" && printf '%s\0' "${files[@]}" | xargs -0 -r sed -i "s|$1|$2|g"); }
sub 'example\.net' "${PUBLIC_DOMAIN:-}"
sub 'homesrv' "${SERVER_NAME:-}"; sub 'Homesrv' "${SERVER_NAME^}"; sub 'HOMESRV' "${SERVER_NAME^^}"
sub '198\.51\.100\.' "${LAN_PREFIX:-}"
sub 'owner@example\.com' "${OWNER_EMAIL:-}"
sub '100000001' "${TELEGRAM_CHAT_ID:-}"
sub 'Exampletown' "${HOME_TOWN:-}"
sub '\bAlex\b' "${OWNER_FIRST_NAME:-}"; sub '\balex\b' "${OWNER_FIRST_NAME,,}"
echo "localized ${#files[@]} files; review with: git diff --stat"
