"""Tests for the cron gate that decides whether this hour is a brief hour.

The bug this file exists for is the quietest one in the repo: the brief fires
on the CONTAINER's clock (agent-mgr's fleet default is America/Los_Angeles)
while every transaction is stamped in the OWNER's zone. Both are right on the
machine that wrote them and nothing reports the disagreement -- someone in
Tokyo just gets their morning brief at midnight, forever.

So the tests worth having here are the ones about zones and about the gate's
two hard promises: never exit non-zero (the scheduler wakes the agent anyway
when the script fails, which turns a crash into twenty-four messages a day)
and never open the same slot twice in one local day.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    monkeypatch.setenv("CFO_DATA", str(tmp_path))
    import importlib
    import money
    importlib.reload(money)
    import brief_gate
    importlib.reload(brief_gate)
    return brief_gate


@pytest.fixture()
def con(gate):
    import money
    c = money.connect()
    money.set_cfg(c, "timezone", "Asia/Tokyo")
    yield c
    c.close()


def at(hour, tz="Asia/Tokyo", day=2):
    from zoneinfo import ZoneInfo
    return datetime(2026, 9, day, hour, 0, tzinfo=ZoneInfo(tz))


# -- the decision ----------------------------------------------------------

def test_opens_on_the_owners_hour_not_the_containers(gate):
    """08:00 in Tokyo is 16:00 the day before in Los Angeles.

    The whole point. A cron expression of `0 8 * * *` in a container the
    fleet defaults to America/Los_Angeles fires at 00:00 Tokyo -- the owner
    gets a "bom dia" in the middle of the night, every night, and nothing
    anywhere logs a complaint.
    """
    hours = {"morning": 8, "evening": None}
    assert gate.decide(at(8), hours, {})[0] == "morning"
    assert gate.decide(at(0), hours, {})[0] is None
    assert gate.decide(at(16), hours, {})[0] is None


def test_a_slot_opens_once_per_local_day(gate):
    hours = {"morning": 8, "evening": None}
    slot, _ = gate.decide(at(8), hours, {})
    opened = {slot: "2026-09-02"}
    assert gate.decide(at(9), hours, opened)[0] is None      # retried tick
    assert gate.decide(at(8, day=3), hours, opened)[0] == "morning"


def test_a_slept_through_hour_still_opens_within_the_grace(gate):
    """The host is a laptop. It is closed at 08:00 more mornings than not."""
    hours = {"morning": 8, "evening": None}
    assert gate.decide(at(9), hours, {})[0] == "morning"
    assert gate.decide(at(11), hours, {})[0] == "morning"
    assert gate.decide(at(12), hours, {})[0] is None


def test_grace_never_crosses_midnight(gate):
    """A 23:00 slot must not open at 01:00 the next day.

    `yesterday` is the brief's first sentence, and at 01:00 it means a
    different day than the one the 23:00 brief was written for.
    """
    hours = {"morning": None, "evening": 23}
    assert gate.decide(at(23), hours, {})[0] == "evening"
    assert gate.decide(at(1, day=3), hours, {})[0] is None


def test_an_off_slot_never_opens(gate):
    assert gate.decide(at(22), {"morning": 8, "evening": None}, {})[0] is None


# -- hours as a ledger setting --------------------------------------------

def test_hours_come_from_the_ledger_and_default_to_eight(gate, con):
    import money
    assert money.brief_hours(con) == {"morning": 8, "evening": None}
    money.set_cfg(con, "brief_hour", "6")
    money.set_cfg(con, "night_brief_hour", "22")
    assert money.brief_hours(con) == {"morning": 6, "evening": 22}


def test_a_hand_mangled_hour_falls_back_to_the_default_not_to_off(gate, con):
    """Reading a typo as "they turned it off" is a silent unsubscribe."""
    import money
    money.set_cfg(con, "brief_hour", "8h")       # only reachable by hand
    assert money.brief_hours(con)["morning"] == 8


def test_the_setter_refuses_an_hour_that_is_not_one(gate, con):
    import money
    for bad in ("8h", "25", "manhã", "-1"):
        with pytest.raises(SystemExit):
            money.validate_cfg("brief_hour", bad)
    assert money.validate_cfg("brief_hour", " 07 ") == "7"
    assert money.validate_cfg("night_brief_hour", "OFF") == "off"
    assert money.validate_cfg("timezone", "8h") == "8h"   # other keys untouched


# -- the two promises to the scheduler ------------------------------------

def _run(tmp_path):
    """The gate exactly as the scheduler runs it: no argv, CFO_DATA in env."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "brief_gate.py")],
        capture_output=True, text=True,
        env={**os.environ, "CFO_DATA": str(tmp_path)},
    )


def _ledger(tmp_path, **settings):
    """A ledger at tmp_path with these settings, written out of process."""
    code = ("import money, sys; c = money.connect(); "
            "[money.set_cfg(c, k, v) for k, v in "
            f"{settings!r}.items()]")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=SCRIPTS,
                   env={**os.environ, "CFO_DATA": str(tmp_path)})


def test_a_closed_hour_prints_the_gate_and_exits_zero(tmp_path):
    """`{"wakeAgent": false}` on the last line, exit 0.

    Both halves matter: the scheduler honours the gate only when the script
    SUCCEEDED (`if _ran_ok and not _parse_wake_gate(...)`), so a non-zero exit
    is not a closed gate -- it is an open one.
    """
    now = datetime.now().astimezone()
    closed = (now.hour + 6) % 24                 # far from now in any zone
    _ledger(tmp_path, timezone=str(now.tzinfo), brief_hour=str(closed),
            night_brief_hour="off")

    r = _run(tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {"wakeAgent": False}


def test_a_broken_ledger_closes_the_gate_and_still_exits_zero(tmp_path):
    """The catch-all closes rather than opens.

    A crash that opened the gate would send the brief every hour of every
    day -- the one failure this agent does not survive.
    """
    (tmp_path / "ledger.db").write_bytes(b"this is not a database")
    r = _run(tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {"wakeAgent": False}


def test_an_open_hour_wakes_the_agent_and_says_which_brief(tmp_path):
    now = datetime.now().astimezone()
    _ledger(tmp_path, timezone=str(now.tzinfo), brief_hour=str(now.hour),
            night_brief_hour="off")

    r = _run(tmp_path)
    assert r.returncode == 0
    last = r.stdout.strip().splitlines()[-1]
    with pytest.raises(json.JSONDecodeError):    # a gate line would skip the run
        json.loads(last)
    assert "morning" in r.stdout

    # and a second tick in the same hour does not send it again
    again = _run(tmp_path)
    assert json.loads(again.stdout.strip().splitlines()[-1]) == {"wakeAgent": False}
