#!/bin/bash
# Installs RemiDex on this Mac: the bot (poke_browser.py) as two always-on launchd jobs, one that does its work on its own
# schedule and one that notes who shows up in the activity feed, and a third that serves the dashboard at
# http://remidex.localhost:8642 (dashboard.py). The first time, it asks before installing, then opens Chrome for you to sign in.
#
#   ./install_browser.sh                           install, or update after you've changed or downloaded new files
#   ./install_browser.sh --check                   only check that this Mac is ready; changes nothing
#   ./install_browser.sh --uninstall               stop and remove RemiDex (your sign-in and history stay)
#   ./install_browser.sh --uninstall --everything  ...and delete your sign-in and everything RemiDex saved
#   add --yes to skip the questions
#
# Everything RemiDex saves lives in ~/Library/Application Support/remilianet-poke-browser, because macOS blocks
# background jobs from reading ~/Documents.
set -euo pipefail

LABEL="local.remilianet-poke-browser"
FEED_LABEL="local.remilianet-feed-watch"
DASH_LABEL="local.remilianet-dashboard"
APP_DIR="$HOME/Library/Application Support/remilianet-poke-browser"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
FEED_PLIST="$HOME/Library/LaunchAgents/$FEED_LABEL.plist"
DASH_PLIST="$HOME/Library/LaunchAgents/$DASH_LABEL.plist"
LOG="$HOME/Library/Logs/remilianet-poke-browser.log"
DASH_LOG="$HOME/Library/Logs/remilianet-dashboard.log"
PLAYWRIGHT_VERSION=1.60.0  # the last release that runs on macOS's built-in Python 3.9
PORT=8642
FILES=(poke_browser.py store.py dashboard.py dashboard.html catalog.json recipes.json quests.py)  # what gets copied to $APP_DIR
HERE="$(cd "$(dirname "$0")" && pwd)"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"  # a test can swap this for `true`, so nothing real is started or stopped

CHECK=0 UNINSTALL=0 EVERYTHING=0 YES=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --everything) EVERYTHING=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done
if [[ $EVERYTHING == 1 && $UNINSTALL == 0 ]]; then
  echo "--everything goes with --uninstall." >&2
  exit 2
fi

stop_jobs() {
  for label in "$LABEL" "$FEED_LABEL" "$DASH_LABEL"; do
    "$LAUNCHCTL" bootout "gui/$(id -u)/$label" 2>/dev/null || true
  done
}

if [[ $UNINSTALL == 1 ]]; then
  if [[ $EVERYTHING == 1 && $YES != 1 ]]; then
    echo "This deletes your RemiliaNET sign-in and everything RemiDex saved on this Mac:"
    echo "  $APP_DIR"
    echo "  $LOG (and $LOG.old)"
    echo "  $DASH_LOG"
    printf "Type yes to delete it all: "
    read -r answer || answer=""
    [[ "$answer" == "yes" ]] || { echo "Nothing was changed."; exit 0; }
  fi
  stop_jobs
  rm -f "$PLIST" "$FEED_PLIST" "$DASH_PLIST"
  for f in "${FILES[@]}"; do rm -f "$APP_DIR/$f"; done
  rm -rf "$APP_DIR/remidex"
  rm -rf "$APP_DIR/venv"
  if [[ $EVERYTHING == 1 ]]; then
    rm -rf "$APP_DIR"
    rm -f "$LOG" "$LOG.old" "$DASH_LOG"
    echo "Removed RemiDex and everything it saved on this Mac."
  else
    echo "Removed RemiDex. Your sign-in, browser profile and history are still in $APP_DIR (run this again with --everything to delete them)."
  fi
  exit 0
fi

# Is this Mac ready? Prints a tick or a cross for each thing, and counts the crosses in $problems.
problems=0
ok()   { echo "  ✓ $1"; }
fail() { echo "  ✗ $1" >&2; problems=$((problems + 1)); }
preflight() {
  echo "Checking this Mac..."
  if [[ "$(uname)" == "Darwin" ]]; then ok "macOS"; else fail "RemiDex only runs on a Mac for now."; fi
  if [[ "$(id -u)" != 0 ]]; then ok "running as you, not as root"; else fail "Run this as yourself, not with sudo."; fi
  if [[ -d "/Applications/Google Chrome.app" ]]; then ok "Google Chrome"; else fail "Google Chrome isn't in /Applications. Install it from google.com/chrome, then run this again."; fi
  if xcode-select -p >/dev/null 2>&1 && /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    ok "Python $(/usr/bin/python3 -c 'import platform; print(platform.python_version())')"
  else
    fail "Apple's command line tools (they include Python 3.9 or newer) aren't installed. Run: xcode-select --install, wait for it to finish, then run this again."
  fi
  local listener
  listener="$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -Fp 2>/dev/null | sed -n 's/^p//p' | head -1 || true)"
  if [[ -z "$listener" ]]; then
    ok "port $PORT is free"
  elif ps -p "$listener" -o command= 2>/dev/null | grep -q "remilianet-poke-browser/.*dashboard.py"; then
    ok "port $PORT is RemiDex's own (it's already installed)"
  else
    fail "Something else is using port $PORT ($(ps -p "$listener" -o comm= 2>/dev/null || echo "another program")). Quit it, then run this again."
  fi
  if curl -s -o /dev/null -I --max-time 8 https://www.remilia.net; then ok "can reach RemiliaNET"; else fail "Can't reach https://www.remilia.net. Are you online?"; fi
}
preflight
if [[ $CHECK == 1 ]]; then
  if [[ $problems == 0 ]]; then echo "All good: this Mac is ready for RemiDex."; exit 0; fi
  echo "Fix the ✗ above, then run this again."
  exit 1
fi
if [[ $problems != 0 ]]; then
  echo "Fix the ✗ above, then run ./install_browser.sh again." >&2
  exit 1
fi

# The first install says what it will do on your account, and waits for a yes.
FRESH=0
[[ -f "$APP_DIR/cookies.json" ]] || FRESH=1
if [[ $FRESH == 1 && $YES != 1 ]]; then
  cat <<'TEXT'

RemiDex acts on your own RemiliaNET account, from this Mac. Everything starts OFF:
you turn on what you want in RemiDex, where each part has its switch.
  - BeetleBoy: claim your free beetle, daily cheese and the Cheeseman's junk, and spend cheese on hunts
  - Pokes and friend requests: poke people, send friend requests and accept the ones you get
  - BeetleCraft: craft only what you pick on the dashboard
  - Profiles: open the profiles of people who show up in the activity feed (RemiliaNET tells them you looked)
Your sign-in stays on this Mac and is only ever used with RemiliaNET. ./install_browser.sh --uninstall removes it all.

TEXT
  printf "Install RemiDex? [y/N] "
  read -r answer || answer=""
  [[ "$answer" =~ ^[Yy] ]] || { echo "Nothing was installed."; exit 0; }
fi

# Set everything up first, then restart the jobs, so a failed update never leaves you with nothing running.
mkdir -p "$APP_DIR"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  echo "Setting up Playwright (one time, about a minute)..."
  /usr/bin/python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check "playwright==$PLAYWRIGHT_VERSION"
for f in "${FILES[@]}"; do cp "$HERE/$f" "$APP_DIR/$f"; done
rm -f "$APP_DIR/settings.ini"  # (older versions had one; everything it held is on the dashboard now)
rm -rf "$APP_DIR/remidex" && mkdir "$APP_DIR/remidex" && cp "$HERE"/remidex/*.py "$APP_DIR/remidex/"  # the bot's parts

# Sign in: the first time, and whenever RemiDex has been signed out (so running this again is how you sign back in).
if [[ $FRESH == 1 ]] || grep -q '"needs_login": true' "$APP_DIR/state.json" 2>/dev/null; then
  "$APP_DIR/venv/bin/python" "$APP_DIR/poke_browser.py" --login
fi

# write_plist PATH LABEL SCRIPT FLAG LOGFILE: an always-on job running SCRIPT FLAG.
write_plist() {
  cat > "$1" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$2</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/venv/bin/python</string>
    <string>$APP_DIR/$3</string>
    <string>$4</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$5</string>
  <key>StandardErrorPath</key>
  <string>$5</string>
</dict>
</plist>
EOF
}

stop_jobs
mkdir -p "$HOME/Library/LaunchAgents"
write_plist "$FEED_PLIST" "$FEED_LABEL" poke_browser.py --watch-feed "$LOG"
write_plist "$PLIST" "$LABEL" poke_browser.py --scheduled "$LOG"
write_plist "$DASH_PLIST" "$DASH_LABEL" dashboard.py --serve "$DASH_LOG"

"$LAUNCHCTL" bootstrap "gui/$(id -u)" "$FEED_PLIST"
"$LAUNCHCTL" bootstrap "gui/$(id -u)" "$PLIST"
"$LAUNCHCTL" bootstrap "gui/$(id -u)" "$DASH_PLIST"

if [[ "$LAUNCHCTL" == "launchctl" ]]; then
  for _ in $(seq 20); do
    curl -s -o /dev/null "http://localhost:$PORT/" && break
    sleep 1
  done
  curl -s -o /dev/null "http://localhost:$PORT/" || echo "RemiDex didn't start yet. If it doesn't within a minute, look at $DASH_LOG" >&2
fi

if [[ $FRESH == 1 ]]; then
  echo
  echo "Done. RemiDex is running: http://remidex.localhost:$PORT"
  echo "Everything is OFF until you switch it on there."
  echo "macOS may say a background item was added: that's RemiDex (System Settings > General > Login Items)."
  echo "To stop and remove it: ./install_browser.sh --uninstall"
  if [[ "$LAUNCHCTL" == "launchctl" ]]; then open "http://remidex.localhost:$PORT" || true; fi
else
  echo "Updated. RemiDex: http://remidex.localhost:$PORT   (log: $LOG)"
fi
