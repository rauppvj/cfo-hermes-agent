#!/usr/bin/env python3
"""The cron gate: is this hour a brief hour for THIS owner, wherever they are?

Hermes fires cron jobs in the container's zone, and agent-mgr's fleet default
is America/Los_Angeles -- a zone that belongs to nobody. The owner's real zone
lives somewhere else entirely: in the ledger, set by the agent in conversation
("moro em Sao Paulo") and used to stamp every transaction's day. Two sources
of truth for one clock, and they agree only when the operator ALSO edits
AGENT_TZ in the home's .env by hand. Nothing anywhere reports it when they
disagree.

The failure that follows is silent and total. Someone in Tokyo installs this,
answers the setup questions, gets a perfectly stamped ledger -- and their
"morning" brief arrives at midnight, every night, until they mute the agent.
A muted agent is a deleted one.

So the schedule stops being a cron expression and becomes a ledger setting.
The job ticks every hour and this script answers the one question a cron
expression cannot: is it a brief hour where the owner actually lives? Every
hour that is not gets `{"wakeAgent": false}` as its last stdout line, which
the scheduler reads as "skip the agent entirely" -- no model run, no delivery,
no cost. Twenty-three cheap no-ops buy a brief that is correct in every zone
on earth, DST included, configured by the owner answering a question instead
of by anyone editing a dotenv.

Two guarantees live here, and both are about the same failure mode:

  * **It never exits non-zero.** The scheduler honours the gate only when the
    script succeeded -- `if _ran_ok and not _parse_wake_gate(...)`. A crash
    here would not silence the brief, it would send it TWENTY-FOUR TIMES A
    DAY, which is the one outcome this agent does not survive. So every path
    out of main() returns 0, and the catch-all closes the gate rather than
    opening it: a brief that stops arriving is logged, complained about, and
    fixed; a brief that arrives hourly is uninstalled.

  * **One open per slot per local day.** A retried tick, a catch-up fire
    after the Mac wakes from sleep, a second gateway -- each reaches the same
    slot, and opening twice sends the brief twice. The last local date each
    slot opened is recorded next to the ledger. The trade is deliberate: if
    the agent's run then fails, that slot is spent for the day. A missing
    brief is recoverable; an unrequested notification is what gets the whole
    agent muted.

Run by the scheduler as `<python> brief_gate.py` with no arguments. Reads the
same ledger money.py does, through money.py, so the owner's zone has exactly
one definition in this repo.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# This file runs from TWO places and money.py is a sibling in only one of
# them. `hermes cron create --script` refuses an absolute path -- "Script path
# must be relative to ~/.hermes/scripts/" -- so the deployed copy is a
# single-file bind at /opt/data/scripts/brief_gate.py, where nothing else from
# this repo is. Trying the sibling first keeps the tests and a local run
# honest; the skills mount is the deployed answer.
for _candidate in (Path(__file__).resolve().parent,
                   Path("/opt/data/skills/cfo-shared/scripts")):
    if (_candidate / "money.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

import money  # noqa: E402  (path fixed above; money.py has no dependencies)

# How late a slot may still open. A Mac that was asleep at 08:00 and wakes at
# 09:30 should still get its morning brief -- the number it carries is no less
# true an hour on. Clamped so a slot never crosses into the next local day,
# where "yesterday" would mean a different day than the one it was written for.
GRACE_HOURS = 3


def state_path() -> Path:
    return money.data_dir() / "brief_gate.json"


def load_opened() -> dict:
    """{slot: last local date it opened}. Unreadable state reads as empty.

    Deliberately forgiving: a truncated or hand-mangled file must not raise,
    because the catch-all in main() closes the gate and a permanently corrupt
    file would then mean no brief ever again. Forgetting costs at most one
    duplicate; refusing costs every brief from here on.
    """
    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:                     # noqa: BLE001
        print(f"warning: unreadable {state_path()}: {exc}", file=sys.stderr)
        return {}


def save_opened(opened: dict) -> None:
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(opened, indent=2, sort_keys=True))
    tmp.replace(state_path())


def decide(now, hours: dict, opened: dict, grace: int = GRACE_HOURS):
    """Which slot this tick opens, or (None, None).

    `now` is the owner's local time, not the container's. A slot opens on the
    hour it is set to and stays open for `grace` hours after, so a tick the
    machine slept through is not lost -- but only until its own local day
    ends, and only once per day.
    """
    today = now.strftime("%Y-%m-%d")
    for name, hour in hours.items():
        if hour is None or opened.get(name) == today:
            continue
        if hour <= now.hour <= min(hour + grace, 23):
            return name, hour
    return None, None


def main() -> int:
    try:
        con = money.connect()
        now = money.now_local(con)
        zone = money.get_cfg(con, "timezone") or str(now.tzinfo)
        hours = money.brief_hours(con)
        opened = load_opened()
        slot, hour = decide(now, hours, opened)

        if slot is None:
            print(json.dumps({"wakeAgent": False}))
            return 0

        opened[slot] = now.strftime("%Y-%m-%d")
        save_opened(opened)
        late = now.hour - hour
        print(f"Brief slot: {slot}. It is {now:%H:%M} on {now:%Y-%m-%d} for the "
              f"owner ({zone}); this slot is set to {hour:02d}:00"
              + (f", so this run is {late}h late" if late else "") + ".")
        return 0
    except Exception:                            # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        # Closed, not open. See the module docstring: the cost of guessing
        # wrong in this direction is one missed brief, and in the other it is
        # twenty-four unrequested messages.
        print(json.dumps({"wakeAgent": False}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
