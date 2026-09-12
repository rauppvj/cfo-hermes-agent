#!/usr/bin/env bash
# Install the Mac-side notification watcher, so the bank's own notifications
# -- mirrored from the iPhone -- reach the agent without anyone typing.
#
# What it sets up: a launchd agent that runs notify_watch.py every minute with
# the system python, appending money-shaped notifications to a file the
# CONTAINER can see. That file is the whole interface between the two halves.
#
# Where that file lives, and why not in the checkout. The agent's home is a
# Docker volume now, so there is no shared directory by accident any more --
# compose.yml mounts one on purpose (`${HOME}/.cfo/inbox`). It is under $HOME
# and not under the repo because **launchd cannot read ~/Desktop or
# ~/Documents**: it runs without the Files-and-Folders grants a terminal has,
# and a watcher whose script or inbox sits in one of those fails to even open
# it ("Operation not permitted") with nothing in the log but that. A checkout
# is exactly as likely to be on the Desktop as anywhere else.
#
# What it cannot do for you, and says so: grant Full Disk Access to that
# python (macOS asks the person, never a script), and turn on iPhone
# notifications on this Mac. Both are one toggle, once.
#
# Usage:  scripts/install-notify.sh                 the current contract
#         scripts/install-notify.sh --home ~/.hermes-cfo    a legacy instance
#         scripts/install-notify.sh --remove
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=cfo
MAC_DIR="$HOME/.cfo"
INBOX=""
REMOVE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --home)   MAC_DIR="${2:?--home needs a directory}"; shift 2 ;;
        --inbox)  INBOX="${2:?--inbox needs a directory}"; shift 2 ;;
        --remove) REMOVE=1; shift ;;
        -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
done
MAC_DIR="${MAC_DIR/#\~/$HOME}"
INBOX="${INBOX:-$MAC_DIR/inbox}"
LABEL="co.plow.cfo-notify.$NAME"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$MAC_DIR/scripts/notify_watch.py"
PY="/usr/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
REAL_PY="$("$PY" -c 'import sys; print(sys.executable)')"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }

if [ "$REMOVE" = 1 ]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    ok "removed $LABEL"
    exit 0
fi

[ "$(uname)" = "Darwin" ] || { echo "this runs on the Mac that mirrors the iPhone"; exit 1; }
case "$MAC_DIR" in
    "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
        echo "refusing $MAC_DIR: launchd cannot open files there (TCC), and the" >&2
        echo "watcher would fail every minute with nothing but a permission error." >&2
        exit 1 ;;
esac
major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$major" -ge 15 ] || warn "macOS $major: iPhone Mirroring needs macOS 15 or newer"

mkdir -p "$HOME/Library/LaunchAgents" "$MAC_DIR/logs" "$MAC_DIR/cfo" "$MAC_DIR/scripts" "$INBOX"
for f in notify_watch.py bankalert.py; do
    cp "$REPO/cfo-shared/scripts/$f" "$MAC_DIR/scripts/$f"
    chmod 0644 "$MAC_DIR/scripts/$f"
done
ok "notify_watch.py and bankalert.py copied to $MAC_DIR/scripts (re-run this after a git pull)"

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
        <string>$MAC_DIR</string>
        <string>--inbox</string>
        <string>$INBOX</string>
    </array>
    <key>StartInterval</key><integer>60</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$MAC_DIR/logs/notify.log</string>
    <key>StandardErrorPath</key><string>$MAC_DIR/logs/notify.log</string>
</dict>
</plist>
XML
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
ok "launchd agent $LABEL, every minute, log at $MAC_DIR/logs/notify.log"

# Is the file this writes the file the agent reads? A watcher that works
# perfectly into a directory nothing is mounted on is the failure this checks:
# both halves succeed, and nothing ever arrives.
probe="$INBOX/.mount-probe"
: > "$probe"
if (cd "$REPO" && docker compose exec -T agent test -f /srv/cfo/inbox/.mount-probe) 2>/dev/null; then
    ok "the container sees this directory at /srv/cfo/inbox"
else
    warn "the container does NOT see $INBOX at /srv/cfo/inbox."
    echo "       compose.yml must mount it:  - \${HOME}/.cfo/inbox:/srv/cfo/inbox"
    echo "       then: docker compose up -d"
fi
rm -f "$probe"

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
if "$PY" "$SCRIPT" --check --home "$MAC_DIR" >/dev/null 2>&1; then
    ok "Notification Center is readable now -- forwarding starts with the next bank alert"
else
    warn "not readable yet -- expected until toggle 1 is done; then it works on the next minute. Check with:"
    echo "       $PY $SCRIPT --check"
    echo "       $PY $SCRIPT --dump 20     # the newest notifications, \$ marks an amount"
fi
