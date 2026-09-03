#!/bin/sh
# Report this instance's token usage to the Agent Index.
#
# What it sends: token counts per day per model, and nothing else -- no
# prompts, no task text, no file paths, no costs, and none of the ledger.
# Read it yourself; the client is one file of standard-library Python at
# /opt/data/scripts/agent_index_client.py.
#
# WHO IT REPORTS AS changed on 2026-09-03, upstream, mid-hackathon: identity
# was a GitHub account proven by device flow, and is now **the container's own
# Plow token**, which the index resolves by asking Plow. The index stopped
# accepting the old key the same day -- an install that still holds one gets
# 401 on every run and lands on the board at zero. So this wrapper reports only
# when PLOW_AGENT_TOKEN is present, and says so in the log when it is not.
#
# Three environment variables decide whether this works, and none of them
# announces itself when wrong:
#
#   PLOW_AGENT_TOKEN -- the credential, and the whole identity. The gateway
#     loads it from the home's .env at boot, so a hermes cron job inherits it
#     and a `docker exec` session does NOT. That asymmetry is why this script
#     logs rather than prints: run by hand it will look unconfigured, and the
#     scheduled run is the one that counts.
#
#   HOME=/opt/data -- the client keeps its collection baseline here. The
#     container runs with HOME=/root, which is the image's own writable layer:
#     every `agent-mgr deploy` recreates it, and losing the baseline re-dumps
#     history onto one day.
#
#   HERMES_HOME=/opt/data -- where state.db is. A wrong path is NOT an error;
#     it reads as zero tokens, which on a public index looks like an agent
#     nobody uses rather than one nobody configured.
#
# AGENT_INDEX_ID names the agent on the index, not this instance. The index's
# unit is the AGENT -- one published thing, many installs -- so every install
# of cfo reports under `cfo`, and the count of distinct installs is the
# leaderboard. It is an override only so a fork can publish under its own id.
set -eu

export HOME=/opt/data
export HERMES_HOME=/opt/data

CLIENT=/opt/data/scripts/agent_index_client.py
MONEY=/opt/data/skills/cfo-shared/scripts/money.py
AGENT="${AGENT_INDEX_ID:-cfo}"
LOG=/opt/data/logs/agent-index.log

# STDOUT STAYS EMPTY. This is a `--no-agent` cron row and hermes delivers such
# a script's stdout verbatim -- "Empty stdout = silent". Anything printed on a
# good run is a notification every hour, forever, which is how an agent gets
# muted. The log file is where a person looks on purpose.
log() {
    mkdir -p "$(dirname "$LOG")"
    printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"
}

# The owner's own switch, checked before anything is collected or sent. In the
# ledger rather than in a dotenv because the ledger is what survives a deploy
# and what the agent can read out when asked whether it is reporting.
if python3 "$MONEY" config usage_reporting 2>/dev/null | grep -q '"off"'; then
    log "opted out -- money.py config usage_reporting off"
    exit 0
fi

if [ ! -f "$CLIENT" ]; then
    log "no client at $CLIENT -- run: agent-mgr deploy <name>"
    exit 0
fi

if [ -z "${PLOW_AGENT_TOKEN:-}" ]; then
    # Not a failure: run by hand this is simply the wrong environment, and an
    # install whose owner never wanted reporting is entitled to be quiet.
    log "no PLOW_AGENT_TOKEN in this environment -- nothing reported."
    log "  (the gateway loads it from the home's .env, so a scheduled run has"
    log "   it and a docker exec does not; to test by hand, pass it in)"
    exit 0
fi

# `|| true` and an explicit status, because `set -e` would take the client's
# non-zero exit -- a 401, a network blip -- and end the script before the line
# saying what went wrong reached the log.
out="$(python3 "$CLIENT" --agent "$AGENT" 2>&1)" || status=$?
log "${out:-（no output）}"
[ "${status:-0}" = 0 ] || log "client exited ${status}"
exit 0
