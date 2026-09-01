"""Tests for the demo seed.

The seed exists to solve one problem: a fresh install has an empty ledger, so
the first question anyone asks it -- "how is my month going?" -- answers
"no data", and they learn nothing about whether the agent is any good. Every
test here defends that property against the calendar moving.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"))


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("CFO_DATA", str(tmp_path))
    import importlib
    import money
    importlib.reload(money)
    import seed_demo
    importlib.reload(seed_demo)
    return seed_demo


@pytest.fixture()
def con(mod):
    import money as m
    c = m.connect()
    m.set_cfg(c, "timezone", "America/Sao_Paulo")
    m.set_cfg(c, "currency", "BRL")
    yield c
    c.close()


def at(mod, monkeypatch, when: datetime):
    """Pin the seeder's idea of today."""
    import money as m
    monkeypatch.setattr(m, "now_local",
                        lambda con: when.replace(tzinfo=m.tz_of(con)))


@pytest.mark.parametrize("today", [
    datetime(2026, 9, 5, 12, 0),    # judging week — the case that broke
    datetime(2026, 9, 1, 9, 0),     # first of a month, one day of history
    datetime(2027, 2, 14, 12, 0),   # a year on, and a short month
    datetime(2027, 1, 3, 12, 0),    # across a year boundary
])
def test_current_month_is_never_empty(mod, con, monkeypatch, today):
    """The bug this file was written for: an anchor frozen at release looks
    fine until the calendar passes it, and then the current month is empty."""
    import money as m
    at(mod, monkeypatch, today)
    mod.seed(con, months=3)

    month = today.strftime("%Y-%m")
    totals = m.month_totals(con, month)
    assert totals["count"] > 0, f"{month} came back empty — cold start is back"


def test_seed_never_writes_the_future(mod, con, monkeypatch):
    import money as m
    today = datetime(2026, 9, 5, 12, 0)
    at(mod, monkeypatch, today)
    mod.seed(con, months=3)

    latest = con.execute("SELECT MAX(day_local) AS d FROM tx").fetchone()["d"]
    assert latest <= "2026-09-05", "a ledger holding next week is not believable"


def test_completed_months_are_identical_whenever_you_seed(mod, con, monkeypatch,
                                                          tmp_path):
    """A month already over must not change because you seeded on a later
    day — the demo video and a reader's terminal have to agree."""
    import money as m
    at(mod, monkeypatch, datetime(2026, 9, 5, 12, 0))
    mod.seed(con, months=3)
    early = m.month_totals(con, "2026-07")

    con.execute("DELETE FROM tx")
    con.execute("DELETE FROM fixed")
    con.commit()

    at(mod, monkeypatch, datetime(2026, 9, 26, 12, 0))
    mod.seed(con, months=3)
    late = m.month_totals(con, "2026-07")

    assert early == late


def test_current_month_only_grows_as_the_month_does(mod, con, monkeypatch):
    import money as m
    at(mod, monkeypatch, datetime(2026, 9, 10, 12, 0))
    mod.seed(con, months=3)
    on_10th = m.month_totals(con, "2026-09")["expense"]

    con.execute("DELETE FROM tx")
    con.execute("DELETE FROM fixed")
    con.commit()

    at(mod, monkeypatch, datetime(2026, 9, 20, 12, 0))
    mod.seed(con, months=3)
    on_20th = m.month_totals(con, "2026-09")["expense"]

    assert on_20th >= on_10th, "the same month shrank as it got longer"


def test_reset_removes_demo_rows_and_spares_real_ones(mod, con, monkeypatch):
    import money as m
    at(mod, monkeypatch, datetime(2026, 9, 5, 12, 0))
    mod.seed(con, months=1)
    mine = m.add_tx(con, 4200, "expense", "food", note="my own lunch")

    mod.reset(con)

    rows = con.execute("SELECT id, source FROM tx").fetchall()
    assert [r["id"] for r in rows] == [mine]
    assert rows[0]["source"] == "chat"


def test_seeded_rows_are_labelled_demo(mod, con, monkeypatch):
    at(mod, monkeypatch, datetime(2026, 9, 5, 12, 0))
    mod.seed(con, months=2)
    sources = {r["source"] for r in con.execute("SELECT DISTINCT source FROM tx")}
    assert sources == {"demo"}, "sample data must be distinguishable from real"
