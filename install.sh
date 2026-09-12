#!/usr/bin/env bash
# Install cfo on this machine, end to end.
#
# What this is, and why it is short now. Until 2026-09-12 this file drove the
# deprecated deployer: clone agent-mgr, register this checkout, deploy,
# activate (a one-time spend that binds a handset and must never run twice),
# up, cron-sync, sign in to a model provider, offer Latch, check. Nine steps,
# three of them interactive, one irreversible -- and a hard prerequisite on the
# GitHub CLI, signed in, because the deployer fetched the chat plugin with it.
# The Agent Index measured what that cost: one install in three completed.
#
# The current contract is three commands, and this file is a careful wrapper
# around them:
#
#     plow-agents login          once per Plow account
#     plow-agents mint <line>    once per agent -- writes ./plow-credentials
#     docker compose up --build  boots it
#
# No GitHub account. No deployer. No registry. No model sign-in: inference
# comes with the Plow credential. The rules this file follows are the ones
# that make an installer safe to re-run:
#
#   * Every step checks whether it is already done and says so instead of
#     doing it again. Re-running after a failure resumes; it does not start
#     over.
#   * Nothing that changes the Plow account happens without a y/N -- minting a
#     credential, and asking Plow for a new line, are both real and both cost
#     something.
#   * It ends by checking its own work, because every failure this install can
#     have is silent.
#
# Usage:  ./install.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLOW_AGENTS_DIR="${PLOW_AGENTS_DIR:-$HOME/.local/share/plow-agents}"
CREDENTIAL="$REPO/plow-credentials"
TOKEN="${XDG_CONFIG_HOME:-$HOME/.config}/plow/token"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '    \033[2m·\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
need_tty() { [ -t 0 ] || die "$1 needs a terminal: re-run ./install.sh from one"; }

# --------------------------------------------------------------------------
# 0. What has to be true before anything is written
# --------------------------------------------------------------------------
step "Checking what this machine already has"

command -v docker >/dev/null || die "docker is not installed -- https://docker.com/get-started"
docker info >/dev/null 2>&1  || die "docker is installed but not running -- start Docker Desktop and re-run"
ok "docker"

docker compose version >/dev/null 2>&1 \
    || die "this docker has no 'compose' subcommand -- update Docker Desktop (Compose v2+)"
ok "docker compose"

command -v git >/dev/null || die "git is not installed -- xcode-select --install"
command -v python3 >/dev/null || die "python3 is not installed -- brew install python@3.12"
ok "git, python3"

# Docker Desktop must come up at login, or the agent is simply off: the
# container restarts itself, but not while the daemon is down. An agent that
# is off answers nothing and reports nothing, and this cost this repo four
# days of a public leaderboard reading zero.
printf '    turn on Docker Desktop → Settings → General → "Start Docker Desktop when you sign in"\n'

# --------------------------------------------------------------------------
# 1. plow-agents -- the credential CLI, which lives outside this repo
# --------------------------------------------------------------------------
step "plow-agents"

if [ -d "$PLOW_AGENTS_DIR/.git" ]; then
    skip "already at $PLOW_AGENTS_DIR"
else
    git clone --quiet https://github.com/plow-pbc/plow-agents.git "$PLOW_AGENTS_DIR"
    ok "cloned to $PLOW_AGENTS_DIR"
fi
PLOW_AGENTS="$PLOW_AGENTS_DIR/bin/plow-agents"
[ -x "$PLOW_AGENTS" ] || die "$PLOW_AGENTS is missing or not executable"
ok "$($PLOW_AGENTS --help >/dev/null 2>&1 && echo "plow-agents runs")"

# --------------------------------------------------------------------------
# 2. The Plow account -- once per person, not once per agent
# --------------------------------------------------------------------------
step "Your Plow account"

if [ -s "$TOKEN" ]; then
    skip "already logged in ($TOKEN)"
else
    need_tty "logging in to Plow"
    cat <<'TXT'
    This prints a phrase to text from the phone on your Plow account. It logs
    THIS MACHINE in; it does not create an agent and does not bind a handset
    to one.

TXT
    "$PLOW_AGENTS" login
    [ -s "$TOKEN" ] || die "login did not write $TOKEN -- read the error above"
    ok "logged in"
fi

# --------------------------------------------------------------------------
# 3. The line the agent answers on
# --------------------------------------------------------------------------
step "The line cfo will answer on"

if [ -f "$CREDENTIAL" ]; then
    skip "a credential is already at $CREDENTIAL -- not minting a second one"
    skip "(to move this agent to another line: plow-agents revoke, then re-run)"
else
    if [ -d "$CREDENTIAL" ]; then
        die "$CREDENTIAL is a DIRECTORY, which means 'docker compose up' ran before
       the credential existed and Docker created the mount target. Fix it:
           docker compose down -v && rmdir '$CREDENTIAL'
       then re-run this script."
    fi
    LINES="$("$PLOW_AGENTS" lines 2>&1 || true)"
    printf '%s\n' "$LINES" | sed 's/^/    /'
    FREE="$(printf '%s\n' "$LINES" | awk -F'\t' '$4 == "free" {print $1}')"
    if [ -z "$FREE" ]; then
        need_tty "asking Plow for a line"
        cat <<'TXT'

    No free line on this account: every line you have already answers as some
    agent, and two agents on one line both reply to the same chat.

    Plow can give this account another assistant line and a chat on it. That
    is a real change to your account and nothing here can undo it.

TXT
        read -r -p "    Ask Plow for a new line now? [y/N] " answer
        case "$answer" in
            [yY]*) "$PLOW_AGENTS" login --new-line ;;
            *) die "stopped. Free a line (plow-agents revoke <line>) or re-run and say yes." ;;
        esac
        LINES="$("$PLOW_AGENTS" lines 2>&1 || true)"
        FREE="$(printf '%s\n' "$LINES" | awk -F'\t' '$4 == "free" {print $1}')"
        [ -n "$FREE" ] || die "still no free line -- 'plow-agents lines' says what this account has"
    fi
    COUNT="$(printf '%s\n' "$FREE" | wc -l | tr -d ' ')"
    if [ "$COUNT" = 1 ]; then
        LINE="$FREE"
    else
        need_tty "choosing a line"
        printf '\n    More than one free line:\n'
        printf '%s\n' "$FREE" | sed 's/^/      /'
        read -r -p "    Which line should cfo answer on? " LINE
        printf '%s\n' "$FREE" | grep -qx "$LINE" || die "$LINE is not one of the free lines above"
    fi
    NUMBER="$(printf '%s\n' "$LINES" | awk -F'\t' -v l="$LINE" '$1 == l {print $3}')"
    need_tty "minting this agent's credential"
    printf '\n    cfo will answer on %s (%s).\n' "$LINE" "${NUMBER:-number unknown}"
    printf '    Minting creates the agent in Plow and writes its credential to\n'
    printf '    %s. Keep that file: it is this agent, and it is not in git.\n\n' "$CREDENTIAL"
    read -r -p "    Mint it? [y/N] " answer
    case "$answer" in
        [yY]*) (cd "$REPO" && "$PLOW_AGENTS" mint "$LINE") ;;
        *) die "stopped before minting. Nothing was created; re-run when you are ready." ;;
    esac
    [ -s "$CREDENTIAL" ] || die "mint did not write $CREDENTIAL -- read the error above"
    ok "credential written"
fi

# --------------------------------------------------------------------------
# 4. Build and start
# --------------------------------------------------------------------------
step "Building and starting the agent"
printf '    The first build pulls a ~500MB base image; give it a few minutes.\n'
(cd "$REPO" && docker compose up --build -d)
ok "container up"

# --------------------------------------------------------------------------
# 5. Did it come up as THIS agent?
# --------------------------------------------------------------------------
# plow-init asks Plow who holds this credential and refuses to start anything
# on an answer it does not understand -- it parks, with the reason, rather
# than booting as whoever the home volume belonged to last. So the boot has
# exactly two outcomes and both are legible.
step "Waiting for it to say who it is"
deadline=$(( $(date +%s) + 180 ))
configured=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    logs="$(cd "$REPO" && docker compose logs agent 2>/dev/null || true)"
    if printf '%s' "$logs" | grep -q 'plow-init: configured'; then
        configured="$(printf '%s' "$logs" | grep -m1 'plow-init: configured')"
        break
    fi
    if printf '%s' "$logs" | grep -q 'plow-init: .*park\|PARKED'; then
        printf '%s\n' "$logs" | grep -i 'park' | tail -3 | sed 's/^/    /'
        die "the agent parked instead of starting -- the line above says why.
       Nothing else in the container runs until that is fixed."
    fi
    sleep 5
done
if [ -n "$configured" ]; then
    ok "$(printf '%s' "$configured" | tail -c 120)"
else
    printf '    still starting after 3 minutes. Follow it with:\n'
    printf '        docker compose logs -f agent\n'
fi

# --------------------------------------------------------------------------
# 6. Every failure this install can have is silent
# --------------------------------------------------------------------------
step "Checking the install"
(cd "$REPO" && docker compose exec -T agent \
    /opt/hermes/.venv/bin/python3 \
    /var/lib/hermes/skills/cfo-shared/scripts/doctor.py) || true

cat <<TXT

$(bold "Installed.") Text the number above from the phone on your Plow account:

    "hi"                          it will set you up in two questions
    "spent 40 on lunch"           or just start logging
    "can I afford a monitor?"     what a purchase does to the month

It replies in whatever language you write to it in.

The same month is also a page on this Mac, redrawn on every change:

    open $REPO/panel/index.html

Put it full-screen on a spare monitor or an old tablet and it stays current
on its own.

To stop typing purchases at all -- card taps, the bank's own notifications --
see docs/AUTOPILOT.md.

    docker compose logs -f agent     follow it
    docker compose up --build -d     after changing anything in this checkout
    docker compose down              stop it, keeping the ledger

Any time something looks off, the check above is one command:

    docker compose exec agent /opt/hermes/.venv/bin/python3 \\
        /var/lib/hermes/skills/cfo-shared/scripts/doctor.py
TXT
