#!/usr/bin/env bash
# Install the Mac-side notification watcher, so the bank's push notifications
# -- mirrored from the iPhone -- reach the agent without anyone typing.
#
# What it sets up: a launchd agent that runs cfo-shared/scripts/notify_watch.py
# every minute with the system python, appending money-shaped notifications
# to ~/.hermes-<name>/inbox/notifications.jsonl, where the container's
# cfo-notify job reads them.
#
# What it cannot do for you, and says so: grant Full Disk Access to that
# python (macOS asks the person, never a script), and turn on iPhone
# notifications on this Mac. Both are one toggle, once.
#
# Usage:  scripts/install-notify.sh [name]        (default: cfo)
#         scripts/install-notify.sh [name] --remove
set -euo pipefail

NAME="${1:-cfo}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$HOME/.hermes-$NAME"
LABEL="co.plow.cfo-notify.$NAME"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Copied into the instance's home, not run from the checkout: launchd runs
# without the Files-and-Folders grants a terminal has, so a script under
# ~/Desktop or ~/Documents fails to even open ("Operation not permitted").
# The home is plain, and it is where the container-side copies live too.
SCRIPT="$HOME_DIR/scripts/notify_watch.py"
PY="/usr/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
REAL_PY="$("$PY" -c 'import sys; print(sys.executable)')"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }

if [ "${2:-}" = "--remove" ]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    ok "removed $LABEL"
    exit 0
fi

[ "$(uname)" = "Darwin" ] || { echo "this runs on the Mac that mirrors the iPhone"; exit 1; }
[ -d "$HOME_DIR" ] || { echo "no $HOME_DIR -- install the agent first (./install.sh $NAME)"; exit 1; }
major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$major" -ge 15 ] || warn "macOS $major: iPhone Mirroring needs macOS 15 or newer"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME_DIR/logs" "$HOME_DIR/inbox" "$HOME_DIR/scripts"
for f in notify_watch.py bankalert.py; do
    cp "$REPO/cfo-shared/scripts/$f" "$HOME_DIR/scripts/$f"
    chmod 0644 "$HOME_DIR/scripts/$f"
done
ok "notify_watch.py and bankalert.py copied to $HOME_DIR/scripts (re-run this after a git pull)"
cat > "$PLIST" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$SCRIPT</string>
        <string>--home</string>
        <string>$HOME_DIR</string>
    </array>
    <key>StartInterval</key><integer>60</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$HOME_DIR/logs/notify.log</string>
    <key>StandardErrorPath</key><string>$HOME_DIR/logs/notify.log</string>
</dict>
</plist>
XML
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
ok "launchd agent $LABEL, every minute, log at $HOME_DIR/logs/notify.log"

bold ""
bold "Two toggles only you can flip, once:"
cat <<TXT

  1. Full Disk Access for the python that reads Notification Center:
       System Settings → Privacy & Security → Full Disk Access → + → add
       $REAL_PY
     (press ⌘⇧G in the file dialog and paste that path -- it is the binary
      /usr/bin/python3 hands off to, and macOS grants access to the binary)

  2. Your iPhone's notifications on this Mac:
       set up iPhone Mirroring once (System Settings → Desktop & Dock → iPhone
       Mirroring, or open the iPhone Mirroring app), then
       System Settings → Notifications → Allow notifications from iPhone → on,
       and make sure your bank apps are allowed there.

TXT
if "$PY" "$SCRIPT" --check --home "$HOME_DIR" >/dev/null 2>&1; then
    ok "Notification Center is readable now -- forwarding starts with the next bank alert"
else
    warn "not readable yet -- expected until toggle 1 is done; then it works on the next minute. Check with:"
    echo "       $PY $SCRIPT --check"
    echo "       $PY $SCRIPT --dump 20     # the newest notifications, \$ marks an amount"
fi
