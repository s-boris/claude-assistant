#!/usr/bin/env bash
# Offline tests for ~/bin/browser (headed Chrome on Xvfb for the browser MCP). Exit 0 = all pass.
# Never touches the real browser: HOME is a temp dir (state, profile, screenshots, VNC password all live under it),
# the display/ports are unused ones (BROWSER_DISPLAY/_CDP_PORT/_VIEW_PORT/_VNC_PORT), BROWSER_CHROME is a fake
# Chrome, and Xvfb, fluxbox, xdpyinfo, pgrep, curl, node, x11vnc, websockify, ffmpeg, sudo, dpkg-query and
# hostname are PATH shims (fake servers are `sleep` processes; curl answers CDP for the fake Chrome only; sudo
# never runs anything). The real ~/.local/state/browser pids are checked untouched at the end.
# BROWSER_BIN=<path> tests another copy.
set -uo pipefail
B=${BROWSER_BIN:-/home/node/bin/browser}
REAL_ST=/home/node/.local/state/browser
T=$(mktemp -d "${TMPDIR:-/tmp}/browser-tests.XXXXXX")
DISPN=173
LOCK=/tmp/.X$DISPN-lock
export FAKE=$T/fake HOME=$T/home BROWSER_DISPLAY=:$DISPN BROWSER_CDP_PORT=19333 BROWSER_VIEW_PORT=16099 \
       BROWSER_VNC_PORT=15999 BROWSER_CHROME=$T/shims/google-chrome-stable
unset BROWSER_SIZE
ST=$HOME/.local/state/browser
PROFILE=$HOME/.local/share/browser/profile
PWFILE=$HOME/.secrets/novnc.txt
lock_was_there=; [ -e "$LOCK" ] && lock_was_there=1
cleanup() {
  local f p
  for f in "$FAKE"/*.pid "$ST"/*.pid; do
    [ -f "$f" ] && p=$(cat "$f") && [ -n "$p" ] && kill -9 "$p" 2>/dev/null
  done
  for f in /proc/[0-9]*/environ; do   # orphans (e.g. the view timer's sleep) carry this run's FAKE= marker
    { tr '\0' '\n' < "$f"; } 2>/dev/null | grep -qxF "FAKE=$FAKE" && { p=${f#/proc/}; kill -9 "${p%/environ}" 2>/dev/null; }
  done
  [ -z "$lock_was_there" ] && rm -f "$LOCK"
  rm -rf "$T"
}
trap cleanup EXIT
mkdir -p "$T/shims" "$FAKE" "$HOME"
real_before=$(for f in "$REAL_ST"/*.pid; do [ -f "$f" ] && p=$(cat "$f") && echo "$f $p $(kill -0 "$p" 2>/dev/null && echo up || echo down)"; done; [ -e "$REAL_ST/disabled" ] && echo disabled)

# ---- shims
ALIVE='alive_pid() { case "$1" in ""|*[!0-9]*) return 1 ;; esac; s=$(cut -d" " -f3 /proc/"$1"/stat 2>/dev/null) && [ -n "$s" ] && [ "$s" != Z ]; }'
mk() { printf '#!/bin/bash\n%s\n%s\n' "$ALIVE" "$2" > "$T/shims/$1"; chmod +x "$T/shims/$1"; }
mk Xvfb 'printf "%s\n" "$*" >> "$FAKE/xvfb.args"; [ -e "$FAKE/xvfb-broken" ] && exit 1; echo $$ > "$FAKE/x.pid"; exec sleep 600'
mk xdpyinfo 'p=$(cat "$FAKE/x.pid" 2>/dev/null) && alive_pid "$p"'
mk fluxbox 'printf "%s\n" "$*" >> "$FAKE/wm.args"; exec sleep 600'
mk google-chrome-stable '[ "${1:-}" = --version ] && { echo "Google Chrome 999.0.0.0 (fake)"; exit 0; }
printf "%s\n" "$*" >> "$FAKE/chrome.args"; echo "LANG=$LANG" >> "$FAKE/chrome.env"
echo $$ > "$FAKE/chrome.pid"; exec sleep 600'
mk pgrep 'printf "%s\n" "$*" >> "$FAKE/pgrep.args"; p=$(cat "$FAKE/chrome.pid" 2>/dev/null) && alive_pid "$p" && { echo "$p"; exit 0; }; exit 1'
mk curl 'url=; method=GET; for a in "$@"; do case "$a" in http://*) url=$a ;; PUT) method=PUT ;; esac; done
printf "%s %s\n" "$method" "$url" >> "$FAKE/curl.log"
case "$url" in "http://127.0.0.1:$BROWSER_CDP_PORT/"*) ;; *) echo "curl shim: unexpected URL $url" >&2; exit 99 ;; esac
p=$(cat "$FAKE/chrome.pid" 2>/dev/null) && alive_pid "$p" || exit 7
case "$url" in
  */json/version) echo "{\"Browser\": \"Chrome/999.0.0.0\", \"webSocketDebuggerUrl\": \"ws://127.0.0.1/x\"}" ;;
  */json/list) echo "[{\"type\":\"page\",\"title\":\"Example Domain\",\"url\":\"https://example.org/\"},{\"type\":\"service_worker\",\"title\":\"SW\",\"url\":\"https://example.org/sw.js\"},{\"type\":\"page\",\"title\":\"\",\"url\":\"about:blank\"}]" ;;
  */json/new\?*) echo "{\"id\": \"T1\"}" ;;
  *) exit 22 ;;
esac'
mk node 'cat > /dev/null; echo "$*" >> "$FAKE/node.log"; p=$(cat "$FAKE/chrome.pid" 2>/dev/null) && kill "$p" 2>/dev/null; exit 0'
mk x11vnc 'printf "%s\n" "$*" >> "$FAKE/vnc.args"; exec sleep 600'
mk websockify 'printf "%s\n" "$*" >> "$FAKE/novnc.args"; exec sleep 600'
mk ffmpeg 'for a in "$@"; do f=$a; done; printf "\x89PNG fake\n" > "$f"'
mk sudo 'printf "%s\n" "$*" >> "$FAKE/sudo.log"; case "$*" in *install*) [ -f "$FAKE/new-version" ] && cp "$FAKE/new-version" "$FAKE/chrome-version" ;; esac; exit 0'
mk dpkg-query 'cat "$FAKE/chrome-version" 2>/dev/null'
mk hostname 'echo 203.0.113.7'
export PATH=$T/shims:$PATH

pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && printf '       got: %s\n' "$2" | head -30; }
eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "$2 (want: $3)"; fi; }
has() { if [[ "$2" == *"$3"* ]]; then ok "$1"; else no "$1" "$2"; fi; }
hasnt() { if [[ "$2" == *"$3"* ]]; then no "$1" "$2"; else ok "$1"; fi; }
lines() { [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0; }
up() { [ -f "$ST/$1.pid" ] && kill -0 "$(cat "$ST/$1.pid")" 2>/dev/null && echo up || echo down; }
fake_up() { p=$(cat "$FAKE/$1.pid" 2>/dev/null) && kill -0 "$p" 2>/dev/null && echo up || echo down; }

echo "# help and usage"
out=$("$B" --help); rc=$?
eq "--help rc 0" "$rc" "0"
has "--help lists the commands" "$out" "browser view on [MIN]"
err=$("$B" bogus 2>&1); rc=$?
eq "unknown command: rc 2" "$rc" "2"
has "unknown command: usage" "$err" "usage: browser start|stop|restart|ensure"
err=$("$B" open 2>&1); rc=$?
eq "open without URL: rc 1" "$rc" "1"
has "open without URL: usage" "$err" "usage: browser open URL"
err=$("$B" view bogus 2>&1); rc=$?
eq "view bogus: rc 1" "$rc" "1"
has "view bogus: usage" "$err" "usage: browser view on [MIN]|off|status"

echo "# everything down"
out=$("$B" status); rc=$?
eq "status down: rc 1" "$rc" "1"
eq "status down: all parts" "$out" "display: down
wm: down
chrome: down
view: off"
err=$("$B" url 2>&1); rc=$?
eq "url while down: rc 1" "$rc" "1"
has "url while down: says so" "$err" "not running (browser start)"
"$B" screenshot "$T/x.png" >/dev/null 2>&1; eq "screenshot while down: rc 1" "$?" "1"

echo "# ensure starts Xvfb, the WM and Chrome"
sleep 0 & dead=$!; wait "$dead"
[ -z "$lock_was_there" ] && printf '%10d\n' "$dead" > "$LOCK"
mkdir -p "$ST"; truncate -s 10000001 "$ST/chrome.log"
"$B" ensure; rc=$?
eq "ensure rc 0" "$rc" "0"
[ -z "$lock_was_there" ] && { [ -e "$LOCK" ] && no "stale X lock of a dead server removed" || ok "stale X lock of a dead server removed"; }
eq "oversized log trimmed" "$(stat -c %s "$ST/chrome.log")" "0"
eq "Xvfb: display, screen, no TCP" "$(cat "$FAKE/xvfb.args")" ":$DISPN -screen 0 1920x1080x24 -dpi 96 -nolisten tcp +extension RANDR"
eq "fluxbox on the same display" "$(cat "$FAKE/wm.args")" "-display :$DISPN"
args=$(cat "$FAKE/chrome.args")
has "chrome: own profile" "$args" "--user-data-dir=$PROFILE "
has "chrome: CDP port" "$args" "--remote-debugging-port=19333 --remote-debugging-address=127.0.0.1"
has "chrome: container flags" "$args" "--no-sandbox --disable-dev-shm-usage"
has "chrome: window size" "$args" "--window-size=1920,1080 --start-maximized about:blank"
hasnt "chrome: no automation flag (navigator.webdriver stays false)" "$args" "--enable-automation"
eq "chrome: Swiss German locale" "$(cat "$FAKE/chrome.env")" "LANG=de_CH.UTF-8"
eq "chrome_pids looks for this profile only" "$(head -n 1 "$FAKE/pgrep.args")" "-u $(id -u) -f ^/opt/google/chrome/chrome .*--user-data-dir=$PROFILE"
eq "prefs: languages, clean exit, no password saving" "$(python3 -I -c 'import json,sys; p = json.load(open(sys.argv[1])); print(p["intl"]["accept_languages"], p["profile"]["exited_cleanly"], p["profile"]["password_manager_enabled"], p["credentials_enable_service"])' "$PROFILE/Default/Preferences")" "de-CH,de,en-US,en True False False"
has "fluxbox config: Chrome maximized, no decorations" "$(cat "$HOME/.fluxbox/apps")" "[Deco] {NONE}"
has "log: chrome up with its version" "$(cat "$ST/browser.log")" "chrome up (Chrome/999.0.0.0)"
eq "pid files of live processes" "$(up xvfb) $(up wm) $(up chrome)" "up up up"
"$B" ensure
eq "ensure again: idempotent (one Xvfb, one Chrome)" "$(lines "$FAKE/xvfb.args") $(lines "$FAKE/chrome.args")" "1 1"

echo "# status, tabs, open, screenshot"
out=$("$B" status); rc=$?
eq "status up: rc 0" "$rc" "0"
has "status: display" "$out" "display: :$DISPN 1920x1080 (Xvfb pid $(cat "$ST/xvfb.pid"))"
has "status: chrome" "$out" "chrome: Chrome/999.0.0.0, CDP http://127.0.0.1:19333"
has "status: tabs" "$out" "  Example Domain"
out=$("$B" url); rc=$?
eq "url rc 0" "$rc" "0"
eq "url: pages only, untitled marked" "$out" "Example Domain
  https://example.org/
(untitled)
  about:blank"
out=$("$B" open "https://example.org/x?a=1"); rc=$?
eq "open rc 0" "$rc" "0"
eq "open: says so" "$out" "opened https://example.org/x?a=1"
has "open: PUT /json/new on the CDP port" "$(cat "$FAKE/curl.log")" "PUT http://127.0.0.1:19333/json/new?https://example.org/x?a=1"
out=$("$B" screenshot "$T/shots/a.png"); rc=$?
eq "screenshot to a file: prints the path" "$rc $out" "0 $T/shots/a.png"
[ -s "$T/shots/a.png" ] && ok "screenshot written" || no "screenshot written"
out=$("$B" screenshot)
has "screenshot default path" "$out" "$HOME/reports/browser/screen-"

echo "# owner view (noVNC)"
err=$("$B" view on abc 2>&1); rc=$?
eq "view on abc: rc 1" "$rc" "1"
has "view on abc: says why" "$err" "minutes must be a number"
out=$("$B" view on 2); rc=$?
eq "view on rc 0" "$rc" "0"
has "view on: URL on the container network" "$out" "view on for 2 min: http://203.0.113.7:16099/vnc.html?autoconnect=1&resize=scale"
eq "VNC password: 8 chars, mode 600" "$(wc -c < "$PWFILE" | tr -d ' ') $(stat -c %a "$PWFILE")" "8 600"
pw=$(cat "$PWFILE")
eq "x11vnc: localhost only, password from a file" "$(cat "$FAKE/vnc.args")" "-display :$DISPN -localhost -rfbport 15999 -passwdfile $PWFILE -forever -shared -noxdamage -noxrecord -quiet"
hasnt "the password is never on a command line" "$(cat "$FAKE/vnc.args" "$FAKE/novnc.args")" "$pw"
eq "websockify: noVNC to the local VNC port" "$(cat "$FAKE/novnc.args")" "--web /usr/share/novnc 0.0.0.0:16099 127.0.0.1:15999"
eq "auto-off timer running" "$(up viewtimer)" "up"
has "auto-off timer: after 2 min" "$(tr '\0' ' ' < /proc/"$(cat "$ST/viewtimer.pid")"/cmdline)" "sleep 120"
has "view status: on" "$("$B" view status)" "view: on, http://203.0.113.7:16099/vnc.html"
timer=$(cat "$ST/viewtimer.pid")
out=$("$B" view off)
eq "view off: says so" "$out" "view off"
eq "view off: vnc, novnc and the timer stopped" "$(up vnc) $(up novnc) $(kill -0 "$timer" 2>/dev/null && echo up || echo down)" "down down down"
eq "view status: off" "$("$B" view status)" "view: off"
"$B" view on 0 >/dev/null
eq "view on 0: no timer" "$(up viewtimer)" "down"
eq "view on again: same password" "$(cat "$PWFILE")" "$pw"
"$B" view off >/dev/null

echo "# update"
echo 999.0.1 > "$FAKE/chrome-version"
out=$("$B" update); rc=$?
eq "update, nothing new: rc 0" "$rc" "0"
eq "update, nothing new: says current" "$out" "chrome 999.0.1 is current"
has "update: only-upgrade google-chrome-stable via sudo apt-get" "$(cat "$FAKE/sudo.log")" "apt-get install -y -qq --only-upgrade google-chrome-stable"
has "update: refreshes only Google's source first" "$(head -n 1 "$FAKE/sudo.log")" "Dir::Etc::sourcelist=/etc/apt/sources.list.d/google-chrome.sources"
eq "update, nothing new: Chrome not restarted" "$(lines "$FAKE/chrome.args")" "1"
echo 999.0.2 > "$FAKE/new-version"
out=$("$B" update); rc=$?
eq "update, new version: rc 0" "$rc" "0"
eq "update, new version: restarts a running Chrome" "$out" "chrome updated 999.0.1 -> 999.0.2
restarted"
eq "update: graceful close over CDP first" "$(lines "$FAKE/node.log")" "1"
eq "update: Chrome started again" "$(lines "$FAKE/chrome.args") $(up chrome)" "2 up"
has "update: logged" "$(cat "$ST/browser.log")" "chrome updated 999.0.1 -> 999.0.2"

echo "# stop, ensure stays off, start, restart"
out=$("$B" stop); rc=$?
eq "stop: rc 0, says so" "$rc $out" "0 stopped"
eq "stop: everything down" "$(fake_up x) $(fake_up chrome) $(up wm)" "down down down"
[ -e "$ST/disabled" ] && ok "stop: marked as stopped on purpose" || no "stop: marked as stopped on purpose"
"$B" ensure
eq "ensure after stop: stays off" "$(lines "$FAKE/chrome.args") $(fake_up chrome)" "2 down"
out=$("$B" status); rc=$?
eq "status after stop: rc 1" "$rc" "1"
has "status after stop: on purpose" "$out" "chrome: down (stopped on purpose; browser start)"
out=$("$B" start); rc=$?
eq "start: rc 0" "$rc" "0"
[ -e "$ST/disabled" ] && no "start: clears the stop mark" || ok "start: clears the stop mark"
has "start: prints status" "$out" "chrome: Chrome/999.0.0.0"
out=$("$B" restart); rc=$?
eq "restart: rc 0, Chrome restarted" "$rc $(lines "$FAKE/chrome.args") $(up chrome)" "0 4 up"

echo "# error path: Xvfb does not come up"
"$B" stop >/dev/null
touch "$FAKE/xvfb-broken"
err=$("$B" start 2>&1); rc=$?
eq "Xvfb broken: rc 1" "$rc" "1"
has "Xvfb broken: says so" "$err" "Xvfb did not come up on :$DISPN"
eq "Xvfb broken: Chrome not started" "$(lines "$FAKE/chrome.args")" "4"
rm -f "$FAKE/xvfb-broken"

echo "# isolation"
eq "every curl went to the test CDP port" "$(grep -vc ':19333/' "$FAKE/curl.log")" "0"
eq "real browser pids unchanged" "$(for f in "$REAL_ST"/*.pid; do [ -f "$f" ] && p=$(cat "$f") && echo "$f $p $(kill -0 "$p" 2>/dev/null && echo up || echo down)"; done; [ -e "$REAL_ST/disabled" ] && echo disabled)" "$real_before"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
