#!/bin/sh
# Report this instance's token usage to the Agent Index.
#
# What it sends: token counts per day per model, and nothing else -- no
# prompts, no task text, no file paths, no costs, and none of the ledger.
# Read it yourself; the client is one file of standard-library Python at
# /opt/data/scripts/agent_index_client.py.
#
# This wrapper exists for two environment variables, and both are the
# difference between a working report and a silent zero:
#
#   HOME=/opt/data
#     The client keeps its credential at ~/.agent-index/token and its
#     collection baseline at ~/.agent-index/hermes-state.json. This container
#     runs with HOME=/root, which is the image's own writable layer -- NOT the
#     mounted home. `agent-mgr deploy` recreates the container (it says
#     "Recreated" every time), so both files would vanish on the next deploy:
#     the credential silently, and the baseline in a way that re-dumps history
#     on the following run. /opt/data is the bind mount to ~/.hermes-<name> on
#     the Mac, which survives everything.
#
#   HERMES_HOME=/opt/data
#     Where state.db is. The client's own README is explicit that a wrong path
#     is NOT an error -- it reads as zero tokens, which on a public index looks
#     like an agent nobody uses rather than one nobody configured. It is
#     already set to this in the container; setting it here too means the
#     wrapper is still right if that ever changes.
#
# AGENT_INDEX_ID names the agent on the index, not this instance. The index's
# unit is the AGENT -- one published thing, many installs -- so every install
# of cfo reports under `cfo`, and the count of distinct installs is what the
# leaderboard is made of. It is an override rather than a constant only so a
# fork can publish under its own id without editing this file.
set -eu

export HOME=/opt/data
export HERMES_HOME=/opt/data

CLIENT=/opt/data/scripts/agent_index_client.py
AGENT="${AGENT_INDEX_ID:-cfo}"

if [ ! -f "$CLIENT" ]; then
    # Not an error worth failing loudly on a schedule: the client is fetched by
    # the deploy hook, so its absence means this instance has not been deployed
    # since the reporting was added. Say what to run, once an hour, to nobody's
    # phone -- this job delivers to `local`.
    echo "no agent-index client at $CLIENT -- run: agent-mgr deploy <name>"
    exit 0
fi

if [ ! -f /opt/data/.agent-index/token ]; then
    # The same reasoning. An install that never signed in reports nothing, and
    # that is a choice its owner is allowed to make -- the agent works either
    # way. Never turn it into a failing job.
    echo "not signed in to the Agent Index -- usage is not being reported."
    echo "to report it:  docker exec -it hermes-<name> env HOME=/opt/data python3 $CLIENT --agent $AGENT --login"
    exit 0
fi

exec python3 "$CLIENT" --agent "$AGENT"
