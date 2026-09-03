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
for f in brief_gate.py panel.py usage_report.sh; do
    cp "cfo-shared/scripts/$f" "$AGENT_HOME/scripts/$f"
    chmod 0644 "$AGENT_HOME/scripts/$f"
done
echo "deployed brief_gate.py, panel.py and usage_report.sh to $AGENT_HOME/scripts"

# The Agent Index client, which usage_report.sh runs. Fetched rather than
# vendored: it is somebody else's file, MIT, one script of standard-library
# Python, and a copy committed here would go stale silently while looking
# current. Fetched ONCE -- a re-download on every deploy would change the
# reporting code under a running instance with no version to point at when it
# behaves differently. Delete the file and deploy again to update it.
#
# Never fatal. An instance whose owner never signs in reports nothing and
# works exactly as well; a network hiccup here must not fail a deploy.
CLIENT="$AGENT_HOME/scripts/agent_index_client.py"
CLIENT_URL="https://raw.githubusercontent.com/plow-pbc/agent-index-client/main/standalone/agent_index_client.py"
if [ -f "$CLIENT" ]; then
    echo "agent-index client already at $CLIENT"
elif curl -fsSL "$CLIENT_URL" -o "$CLIENT.tmp" 2>/dev/null; then
    # Downloaded to a temp name and renamed, so an interrupted fetch cannot
    # leave a half-file that then never re-downloads (the check above would
    # find it and skip).
    mv "$CLIENT.tmp" "$CLIENT"
    chmod 0644 "$CLIENT"
    echo "fetched the agent-index client to $CLIENT"
else
    rm -f "$CLIENT.tmp"
    echo "could not fetch the agent-index client -- usage will not be reported."
    echo "  retry with: agent-mgr deploy <name>, or curl it yourself:"
    echo "  curl -fsSL $CLIENT_URL -o $CLIENT"
fi

# Both are cron scripts and both are copies for the reason above; neither
# holds state. panel.py reads the ledger and the language setting at run time
# exactly as the gate reads the owner's hours, so a copy left over from an
# older deploy renders the current month from the current data.

# Match the latch declaration to whether this instance actually has a Latch.
#
# Latch is optional here, and most installs will not have it: a statement can
# be sent to the agent as a chat attachment, which is the same import with no
# Mac app and no pair of secrets. But a declared MCP server with no credential
# is not a neutral leftover -- the gateway tries it on every boot, fails, and
# parks it, so the instance carries a permanent broken connection and a log
# full of it, which is the opposite of "this feature is off".
#
# `enabled` is honoured by the loader (mcp_tool filters on it before any
# connect) and an unresolved ${VAR} is left as a literal rather than raising,
# so the block itself is safe to keep in place -- and keeping it is what lets
# `agent-mgr set-latch` still work later, since that command refuses an agent
# whose config declares no latch. Run set-latch, deploy again, and this flips
# it back on.
python3 - <<'PY'
import os, pathlib, sys

home = pathlib.Path(os.environ["AGENT_HOME"])
config, dotenv = home / "config.yaml", home / ".env"

credentialled = False
if dotenv.is_file():
    for line in dotenv.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "DOMO_DEVICE_UID" and value.strip():
            credentialled = True

lines = config.read_text().splitlines(keepends=True)
try:
    start = next(i for i, l in enumerate(lines) if l.rstrip() == "  latch:")
except StopIteration:
    sys.exit("config.yaml declares no `  latch:` block -- this hook is out of "
             "date with the config it is editing, so nothing was changed")

want = "true" if credentialled else "false"
for i in range(start + 1, len(lines)):
    line = lines[i]
    if line.strip() and not line.startswith("    "):
        sys.exit("the latch block ended with no `enabled:` line -- refusing to "
                 "guess; a latch that is neither on nor off is the broken "
                 "state this edit exists to prevent")
    if line.strip().startswith("enabled:"):
        lines[i] = f"    enabled: {want}\n"
        break

config.write_text("".join(lines))
print(f"latch {'enabled -- DOMO_DEVICE_UID is set' if credentialled else 'disabled -- no DOMO_DEVICE_UID in this home'}")
PY
