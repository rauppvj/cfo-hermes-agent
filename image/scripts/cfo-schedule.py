#!/usr/bin/env python3
"""Put this install's two scheduled jobs in place, and keep them there.

What this replaces. Under the deprecated deployer the two rows came from
`agent-mgr cron-sync` reading runtime-cron.json, which meant they existed only
on a machine that had run that command, drifted the moment anyone edited a
prompt by hand, and were refused by cron-sync once they had. The current
contract has no deployer at all: `docker compose up` is the install, and
`hermes cron` persists jobs to $HERMES_HOME/cron/jobs.json -- a file on the
home volume that NO rebuild replays. So an install whose crons are registered
by hand is an install where the brief stops arriving after the first rebuild,
on a machine where everything else looks right.

Two rows, and both have to wake the model, which is why they are cron rows
rather than services like the panel and the reporter:

  * cfo-brief  -- hourly tick, gated. brief_gate.py answers the question a
    cron expression cannot ("is it 08:00 where the OWNER lives?") and closes
    the gate on the other 23 hours, so the model is not woken.
  * cfo-notify -- every five minutes, gated the same way by notify_gate.py:
    it returns {"wakeAgent": false} unless the Mac's watcher actually left a
    bank notification in the inbox.

Idempotence reads jobs.json, never `hermes cron list`. The listing is a
human-readable table, and matching on its text cost the reference agent three
review rounds of guards -- whole-word matching, a name regex, floors for
partial parses -- none of which reach the bottom, because a table is not a
data structure. Worse, it fails in the expensive direction: read "I could not
tell what is registered" as "nothing is" and every run adds a second copy of
both rows, which is a brief twice an hour. jobs.json has `name` as a field.

Two modes, because the two halves need different uids -- and getting that
backwards is a 14-hour outage upstream already paid for:

  * `stage`    copies the gates into the home as root, so what the scheduler
               runs is restored under whatever a turn may have left there.
  * `register` creates the rows AS THE AGENT'S UID. `hermes cron create`
               rewrites jobs.json by atomic replace, so doing it as root
               leaves the schedule root-owned 0600 and the gateway -- which
               is the scheduler, and runs as hermes -- then fails every tick
               reading its own file.

Run by the cfo-schedule service after plow-init: `stage` once at start,
`register` every five minutes, so a row deleted by hand or lost with a home
comes back on its own.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

HOME = pathlib.Path(os.environ.get("HERMES_HOME", "/var/lib/hermes"))
JOBS = HOME / "cron" / "jobs.json"
SCRIPTS = HOME / "scripts"
# The root-owned copies. What a supervisor or a cron row runs must not be a
# file a turn can rewrite: everything under $HERMES_HOME/skills belongs to the
# agent's uid in a running container, so scheduling that copy would turn one
# prompt-injected edit into code that runs unattended forever, holding this
# agent's credential and the owner's ledger.
SOURCE = pathlib.Path(os.environ.get("CFO_SCRIPT_SOURCE", "/opt/plow/cfo-shared/scripts"))
HERMES = os.environ.get("CFO_HERMES_BIN", "/opt/hermes/.venv/bin/hermes")
# The agent's uid and gid in every Plow base image.
AGENT_UID, AGENT_GID = 10000, 10000

# hermes resolves --script against $HERMES_HOME/scripts and refuses anything
# that resolves outside it -- not an absolute path, not a symlink out. So the
# gates are COPIED there, root-owned 0644 on every pass: readable by the uid
# the scheduler runs as, and restored under anything the agent may have left
# in their place. money.py travels with them because both gates import it.
GATES = ("brief_gate.py", "notify_gate.py", "money.py", "bankalert.py")

JOBS_WANTED = (
    {
        "name": "cfo-brief",
        "schedule": "0 * * * *",
        "script": "brief_gate.py",
        "skills": ("cfo-brief",),
        "prompt": (
            "Run the cfo-brief skill and send the brief named in the script "
            "output above, written in the language it names. Run commands with "
            "the terminal tool (execute_code is blocked under cron). If there "
            "is nothing worth reporting, send nothing rather than padding it."
        ),
        "deliver": "home",
    },
    {
        "name": "cfo-notify",
        "schedule": "*/5 * * * *",
        "script": "notify_gate.py",
        "skills": ("cfo-log",),
        "prompt": (
            "The script output above lists bank notifications the owner's Mac "
            "forwarded. Lines marked ✓ and = were already recorded by "
            "code: do NOT log them again. Reply with ONE line, in the language "
            "named, saying what was recorded. Run commands with the terminal "
            "tool. Never invent an amount."
        ),
        "deliver": "home",
    },
)


def say(message: str) -> None:
    """One line on stderr, which is where a supervised service's log is."""
    print(f"cfo-schedule: {message}", file=sys.stderr, flush=True)


def stage_gates() -> None:
    """Copy the gates into the one directory hermes will run a script from.

    The directory is the agent's -- the runtime writes there too -- so it is
    created owned by the agent's uid. The FILES in it stay root's: 0644 is
    readable by the uid the scheduler runs as, and unwritable by the turn that
    could otherwise replace what runs unattended every five minutes.
    """
    if not SCRIPTS.exists():
        SCRIPTS.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(SCRIPTS, AGENT_UID, AGENT_GID)
        except PermissionError:                      # not root: a hand run
            pass
        os.chmod(SCRIPTS, 0o755)
    for name in GATES:
        source = SOURCE / name
        if not source.is_file():
            say(f"{source} is not in this image -- {name} will not run")
            continue
        target = SCRIPTS / name
        # Never through an existing path: the agent owns this directory and
        # could have left a symlink at that name, and this runs as root.
        if target.is_symlink():
            target.unlink()
        temporary = target.with_suffix(target.suffix + ".tmp")
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)


def registered_names() -> set[str]:
    """The names hermes has, from its own state.

    A missing file is an empty schedule -- that is a fresh home, and the right
    answer is "register them". A file that exists and does not parse is NOT:
    it raises, the caller registers nothing, and the next pass tries again.
    Registering over an unreadable schedule is how one row becomes four.
    """
    if not JOBS.exists():
        return set()
    with JOBS.open(encoding="utf-8") as handle:
        data = json.load(handle)
    rows = data.get("jobs", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"{JOBS} holds {type(rows).__name__}, not a list of jobs")
    return {str(row.get("name", "")) for row in rows if isinstance(row, dict)}


def home_channel() -> str:
    """The chat this agent's owner holds, as plow-init resolved it this boot.

    Not a config value and not ours to guess: the image asks Plow who holds
    this credential and publishes the answer. Empty means the identity is not
    up yet, and a job created with an empty deliver target delivers nowhere --
    silently, forever, because the row then looks registered. So an empty one
    is a reason to wait for the next pass, never to register.
    """
    published = pathlib.Path("/run/s6/container_environment/PLOW_HOME_CHANNEL")
    if published.is_file():
        return published.read_text().strip()
    return os.environ.get("PLOW_HOME_CHANNEL", "").strip()


def create(job: dict, channel: str) -> bool:
    argv = [HERMES, "cron", "create", job["schedule"], job["prompt"],
            "--name", job["name"], "--script", job["script"]]
    for skill in job["skills"]:
        argv += ["--skill", skill]
    if job["deliver"] == "home":
        argv += ["--deliver", f"plow_chat:{channel}"]
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        say(f"{job['name']} was not created: {done.stderr.strip() or done.stdout.strip()}")
        return False
    say(f"registered {job['name']} ({job['schedule']})")
    return True


def main(argv: list[str] | None = None) -> int:
    """`stage`, `register`, or both when neither is asked for."""
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a not in ("--stage-only", "--register-only")]
    if unknown:
        say(f"unknown argument {unknown[0]} -- takes --stage-only or --register-only")
        return 2
    if "--register-only" not in argv:
        stage_gates()
    if "--stage-only" in argv:
        return 0
    try:
        have = registered_names()
    except Exception as error:                       # noqa: BLE001 -- see docstring
        say(f"cannot read {JOBS} ({error}) -- registering nothing this pass")
        return 1
    missing = [job for job in JOBS_WANTED if job["name"] not in have]
    if not missing:
        return 0
    channel = home_channel()
    if not channel and any(job["deliver"] == "home" for job in missing):
        say("no PLOW_HOME_CHANNEL published yet -- waiting rather than "
            "registering a job that would deliver nowhere")
        return 1
    return 0 if all(create(job, channel) for job in missing) else 1


if __name__ == "__main__":
    sys.exit(main())
