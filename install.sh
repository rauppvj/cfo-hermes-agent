#!/usr/bin/env bash
# Install this agent, end to end, from a fresh checkout.
#
# What it replaces is a ten-command sequence where every step is a place to
# stop: install agent-mgr, register, deploy, activate, up, cron-sync, sign-in,
# set-latch, check-latch -- in that order, with two of them interactive and one
# of them (activate) a ONE-TIME SPEND that must never run twice.
#
# The rules this script follows, because they are what makes an installer
# safe to re-run:
#
#   * Every step checks whether it is already done, and says so instead of
#     doing it again. Re-running this file after a failure resumes; it does
#     not start over.
#   * `activate` is guarded hardest. It mints a credential, sends a DM, and
#     binds the agent permanently to the handset that texts the code. If the
#     home already carries PLOW_HOME_CHANNEL, it is skipped, loudly.
#   * Nothing is done silently on the owner's behalf that costs money, sends
#     a message, or writes a credential without saying so first.
#
# Usage:  ./install.sh [name]        (default name: cfo)
set -euo pipefail

NAME="${1:-cfo}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MGR_DIR="${AGENT_MGR_DIR:-$HOME/services/agent-mgr}"
BIN_DIR="${AGENT_MGR_BIN:-$HOME/.local/bin}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '    \033[2m·\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# 0. What has to be true before anything is written
# --------------------------------------------------------------------------
step "Checking what this machine already has"

# Demanded only by the steps that actually stop and ask -- activation, the
# model sign-in, the Latch offer. A re-run with all three already done needs
# no terminal at all, which is what makes this safe to put in a provisioning
# script once the interactive part is behind you.
need_tty() { [ -t 0 ] || die "$1 needs a terminal: re-run ./install.sh $NAME from one"; }

command -v docker >/dev/null || die "docker is not installed -- https://docker.com/get-started"
docker info >/dev/null 2>&1  || die "docker is installed but not running -- start Docker Desktop and re-run"
ok "docker"

command -v git    >/dev/null || die "git is not installed"
command -v python3 >/dev/null || die "python3 is not installed"
python3 - <<'PY' || die "python3 is older than 3.11 -- the ledger engine needs it"
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
ok "python3 $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

command -v gh >/dev/null || die "the GitHub CLI is not installed -- https://cli.github.com (agent-mgr fetches the Plow Chat plugin with it)"
gh auth status >/dev/null 2>&1 || die "gh is installed but not signed in -- run 'gh auth login' and re-run this"
ok "gh, authenticated"

# --------------------------------------------------------------------------
# 1. agent-mgr -- the deployer, which lives outside this repo
# --------------------------------------------------------------------------
step "agent-mgr"

if [ ! -d "$MGR_DIR/.git" ]; then
    git clone --quiet https://github.com/plow-pbc/agent-mgr.git "$MGR_DIR"
    ok "cloned to $MGR_DIR"
else
    skip "already at $MGR_DIR"
fi

mkdir -p "$BIN_DIR"
ln -sf "$MGR_DIR/agent-mgr" "$BIN_DIR/agent-mgr"
export PATH="$BIN_DIR:$PATH"
command -v agent-mgr >/dev/null || die "agent-mgr is not on PATH even after linking it into $BIN_DIR"
ok "agent-mgr on PATH"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf '    add %s to your PATH to use agent-mgr later\n' "$BIN_DIR" ;;
esac

# --------------------------------------------------------------------------
# 2. Register and deploy -- both safe to have run before
# --------------------------------------------------------------------------
step "Registering $NAME"

registered_repo="$(agent-mgr --json ls 2>/dev/null \
    | python3 -c "
import json, sys
rows = json.load(sys.stdin)['result']['agents']
print(next((r['repo'] for r in rows if r['name'] == '$NAME'), ''))
" 2>/dev/null || true)"

if [ -z "$registered_repo" ]; then
    agent-mgr register "$NAME" "$REPO"
    ok "registered $NAME -> $REPO"
elif [ "$registered_repo" != "$REPO" ]; then
    die "$NAME is already registered against $registered_repo, not this checkout.
       Run this from that checkout, or pick another name: ./install.sh mycfo"
else
    skip "already registered"
fi

RESOLVED="$(agent-mgr --json resolve "$NAME")"
resolved_field() { printf '%s' "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['$1'])"; }
HOME_DIR="$(resolved_field home)"
CONTAINER="$(resolved_field container)"
DOTENV="$HOME_DIR/.env"

step "Deploying"
AGENT_TRANSITION_ACK=1 agent-mgr deploy "$NAME"
ok "config, plugin, skills and the brief gate are in $HOME_DIR"

# --------------------------------------------------------------------------
# 3. Activation -- the one step that cannot be undone
# --------------------------------------------------------------------------
step "Claiming the agent from your phone"

dotenv_value() { [ -f "$DOTENV" ] && sed -n "s/^$1=//p" "$DOTENV" | tail -1 || true; }

if [ -n "$(dotenv_value PLOW_HOME_CHANNEL)" ]; then
    skip "already activated -- not re-running it (a second activation mints another credential and DMs you again)"
else
    need_tty "claiming the agent"
    cat <<'TXT'
    This prints a code. Text it from the phone that should OWN this agent --
    that handset becomes the owner permanently, and the activation is a
    one-time spend. Use the right phone.

TXT
    read -r -p "    Ready? [y/N] " answer
    case "$answer" in
        [yY]*) agent-mgr activate "$NAME" ;;
        *) die "stopped before activating. Re-run this script when you are ready -- everything above is already done." ;;
    esac
    [ -n "$(dotenv_value PLOW_HOME_CHANNEL)" ] || die "activation did not write a home channel -- read the error above; do NOT re-run activate blindly"
    ok "activated"
fi

# --------------------------------------------------------------------------
# 4. Start it, and give it a schedule and a model credential
# --------------------------------------------------------------------------
step "Starting the container"
AGENT_TRANSITION_ACK=1 agent-mgr up "$NAME"
ok "running"

step "Registering the brief and the panel"
agent-mgr cron-sync "$NAME"
ok "hourly tick; the gate opens it at your own 08:00 and 22:00"
ok "the panel redraws every ten minutes, without waking the model"

# Render it once now, so there is something to open before the first message
# -- but only over a ledger that exists. On a fresh install the container has
# not created one yet, and making it here from the host is how two processes
# end up disagreeing about who owns the file.
PANEL="$HOME_DIR/cfo/panel/index.html"
if [ -f "$HOME_DIR/cfo/ledger.db" ]; then
    CFO_DATA="$HOME_DIR/cfo" python3 "$REPO/cfo-shared/scripts/panel.py" 2>/dev/null \
        && ok "panel at $PANEL"
else
    skip "panel appears at $PANEL after your first message"
fi

step "Signing in to the model provider"
if [ -s "$HOME_DIR/auth.json" ]; then
    skip "a credential is already in $HOME_DIR/auth.json"
else
    need_tty "signing in to the model provider"
    echo "    A device code follows. Open the URL, enter the code, come back."
    agent-mgr sign-in "$NAME"
    ok "signed in"
fi

# --------------------------------------------------------------------------
# 4b. Counting this install on the Agent Index -- optional, and asked plainly
# --------------------------------------------------------------------------
#
# The credential is per INSTALL and belongs to the person installing: their
# GitHub approves it, it is stored in their own home, and it is what makes
# this instance count as one install of `cfo` rather than as nobody. It is
# never baked into anything -- see the client's own README on exactly that.
step "Reporting usage to the Agent Index (optional)"

if [ -f "$HOME_DIR/.agent-index/token" ]; then
    skip "already signed in -- usage is being reported hourly"
elif [ ! -f "$HOME_DIR/scripts/agent_index_client.py" ]; then
    skip "no client on this machine -- the deploy could not fetch it; re-run 'agent-mgr deploy $NAME'"
elif [ ! -t 0 ]; then
    skip "no terminal -- skipping; the agent works the same, nothing is reported"
else
    cat <<'TXT'
    This agent is published on the AI Worth Using Agent Index, and installs
    are counted there. Signing in reports HOW MUCH the agent ran -- token
    counts per day, per model.

    It does NOT send your ledger, your transactions, your prompts, or
    anything you text the agent. The client is one readable file:
    ~/.hermes-<name>/scripts/agent_index_client.py

    Skip it and everything works exactly the same.

TXT
    read -r -p "    Sign in and report usage? [y/N] " answer
    case "$answer" in
        [yY]*)
            docker exec -it "$CONTAINER" env HOME=/opt/data python3 \
                /opt/data/scripts/agent_index_client.py --agent cfo --login \
                && ok "signed in -- this install now counts" \
                || skip "sign-in did not complete; re-run ./install.sh $NAME to try again"
            ;;
        *) skip "skipped -- nothing is reported" ;;
    esac
fi

# --------------------------------------------------------------------------
# 5. Latch -- optional, and asked for last on purpose
# --------------------------------------------------------------------------
step "Plow Latch (optional)"

if [ -n "$(dotenv_value DOMO_DEVICE_UID)" ]; then
    skip "already configured"
    agent-mgr check-latch "$NAME" || true
else
    cat <<'TXT'
    Latch lets the agent reach your Mac -- it can then pick a statement out
    of your Downloads itself. You do not need it: you can send the file in
    the chat and it reads it the same way.

    Skip this now and add it any time with:
        agent-mgr set-latch <name> && agent-mgr deploy <name>

TXT
    if [ ! -t 0 ]; then
        skip "no terminal -- skipping the Latch offer; add it later with 'agent-mgr set-latch $NAME'"
        answer=n
    else
        read -r -p "    Set up Latch now? [y/N] " answer
    fi
    case "$answer" in
        [yY]*)
            agent-mgr set-latch "$NAME"
            AGENT_TRANSITION_ACK=1 agent-mgr deploy "$NAME"
            agent-mgr check-latch "$NAME"
            ok "latch reachable"
            ;;
        *) skip "skipped -- send statements as chat attachments instead" ;;
    esac
fi

# --------------------------------------------------------------------------
# Done
# --------------------------------------------------------------------------
cat <<TXT

$(bold "Installed.") Text your agent from the phone you activated with:

    "hi"                          it will offer to set you up in 30 seconds
    "spent 40 on lunch"           or just start logging and set up later
    "can I afford a monitor?"     what a purchase does to the month

It replies in whatever language you write to it in.

It will ask what city you are in. That answer sets the timezone for
everything, including the hour your brief arrives -- 08:00 and 22:00 where
you live, wherever that is.

The same month is also a page on this Mac, redrawn on every change:

    open $HOME_DIR/cfo/panel/index.html

Put it full-screen on a spare monitor or an old tablet and it stays current
on its own.

    agent-mgr logs $NAME          follow it
    agent-mgr restart $NAME       after changing anything in this checkout
TXT
