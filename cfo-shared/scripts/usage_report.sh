#!/bin/sh
# Report this instance's token usage to the Agent Index.
#
# What it sends: token counts per day per model, and nothing else -- no
# prompts, no task text, no file paths, no costs, and none of the ledger.
# Read it yourself; the client is one file of standard-library Python at
# /opt/data/scripts/agent_index_client.py.
#
# WHO IT REPORTS AS changed TWICE on 2026-09-03, upstream, mid-hackathon.
# Identity was a GitHub account proven by device flow; then the container's own
# Plow token, sent as the bearer; and is now an **Index key this install mints
# once**. The Plow token is exchanged at Plow for a short-lived assertion,
# `--register` trades that assertion for an `aik_` key, and every report after
# it carries the key alone. Each change made the previous credential a 401 the
# same day, which is a silent landing on a public leaderboard at zero.
#
# So the gate here is THE KEY, not the Plow token. Gating on PLOW_AGENT_TOKEN
# (as this did between the second and third change) is wrong in the direction
# that costs the metric: an install holding a perfectly good key, whose gateway
# happens not to export the Plow token, would skip every run and report nothing.
#
# Registration is the one step that still needs the Plow token, it is one-time,
# and it is not this script's job -- see the README.
#
# Two environment variables decide whether this works, and neither one
# announces itself when wrong:
#
#   HOME=/opt/data -- where the client reads the minted key
#     (~/.agent-index/token) and keeps its collection baseline. The container
#     runs with HOME=/root, in the image layer that every `agent-mgr deploy`
#     recreates: the key would vanish with it, silently.
#
# (PLOW_AGENT_TOKEN is no longer read here at all. For the record, the gateway
#     loads it from the home's .env at boot, so a hermes cron job inherits it
#     and a `docker exec` session does NOT -- which is why registering by hand
#     takes passing it in explicitly. This script still logs rather than
#     prints, because the scheduled run is the one that counts and it has no
#     terminal to print to.)
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
# The minted Index key, and the only credential a report needs. Under
# HOME=/opt/data this is the bind mount, so it survives `agent-mgr deploy`.
KEY=/opt/data/.agent-index/token

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

# MINT THE KEY IF THIS INSTALL HAS NONE. Upstream saves a key in exactly one
# place -- inside `--register` -- and registering CLAIMS an agent id, which
# only the person who published it can do. Every other install of cfo therefore
# reaches the report with no key and exits, and the message it exits with says
# "no PLOW_AGENT_TOKEN" on a machine that has one. Silent, and it costs the
# only thing the index measures: the count of distinct installs that report.
#
# Minting is its own server call (POST /v1/keys, scope usage,stories) and does
# not touch registration, so this is the missing step rather than a workaround.
# It runs AFTER the opt-out check above: an owner who turned reporting off has
# already left, and no credential is created for them.
#
# The client is imported rather than reimplemented: it owns the URL policy, the
# refusal to follow redirects, the shape check on a key and the atomic write.
# Duplicating those here would be a second copy to keep correct.
if [ ! -s "$KEY" ]; then
    out="$(python3 - "$CLIENT" "$AGENT" 2>&1 <<'PY'
import importlib.util, sys
path, agent = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("aic", path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for name in ("index_assertion", "save_key", "_post", "API"):
    if not hasattr(m, name):
        sys.exit(f"the client no longer exposes {name}() -- register by hand")
code, body = m._post(m.API + "/v1/keys", {"label": agent}, m.index_assertion())
if code != 200 or not m.AGENT_KEY.match(str(body.get("key", ""))):
    sys.exit(f"key mint refused: {code} {body}")
m.save_key(body["key"])
print("minted an Index key for this install")
PY
)" || status=$?
    log "${out:-（no output）}"
    if [ ! -s "$KEY" ]; then
        log "  no key and none could be minted -- nothing reported this run."
        exit 0
    fi
fi

# `|| true` and an explicit status, because `set -e` would take the client's
# non-zero exit -- a 401, a network blip -- and end the script before the line
# saying what went wrong reached the log.
out="$(python3 "$CLIENT" --agent "$AGENT" 2>&1)" || status=$?
log "${out:-（no output）}"
[ "${status:-0}" = 0 ] || log "client exited ${status}"
exit 0
