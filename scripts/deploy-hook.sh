#!/bin/sh
# Install the cron gate into the instance's home.
#
# Everything else this repo ships is MOUNTED -- the skills, SOUL.md -- so a
# `git pull` is the update and nothing can go stale. The gate is the one
# exception, and not by choice: hermes validates a cron job's script path at
# the API boundary and accepts only a real file inside HERMES_HOME/scripts.
#
#   - an absolute path is refused outright ("Script path must be relative to
#     ~/.hermes/scripts/"), even though the scheduler itself resolves one
#   - `../skills/...` is refused as traversal
#   - a symlink is refused too: containment is checked with Path.resolve(),
#     which follows it straight back out of the directory
#   - and a bind mount cannot create the file either. /opt/data is already a
#     bind of the home, and Docker cannot make a mountpoint inside it for a
#     file the host does not have: "mountpoint ... is outside of rootfs".
#
# So it is a copy, refreshed on every `agent-mgr deploy`. That is a smaller
# cost than it looks: the gate reads its hours and the owner's zone from the
# LEDGER at run time, so a copy from an older deploy still opens at the right
# hour in the right zone. What changes here is the mechanism, not the schedule.
#
# Runs with cwd = the repo and AGENT_HOME in the environment (agent-mgr).
set -eu

: "${AGENT_HOME:?deploy hook needs AGENT_HOME -- agent-mgr sets it}"

mkdir -p "$AGENT_HOME/scripts"
cp cfo-shared/scripts/brief_gate.py "$AGENT_HOME/scripts/brief_gate.py"
chmod 0644 "$AGENT_HOME/scripts/brief_gate.py"
echo "deployed brief_gate.py to $AGENT_HOME/scripts"
