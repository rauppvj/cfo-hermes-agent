"""Tests for the wall panel and the two engine functions it needed.

The panel is the one surface nobody is reading when it goes wrong. A chat
message with a bad number gets questioned in the next reply; a page on a
fridge shows the same bad number all day to someone who is walking past it
with their hands full. So the tests that earn their place here are about
what the page is allowed to claim, not about how it looks:

  * it must not print a projection the ledger cannot support -- the rule
    `money.basis()` exists for, now enforced somewhere no human reviews;
  * it must escape everything that came out of an imported statement, where
    a payee named `<script>` is markup in a browser and a curiosity in a
    chat;
  * and rendering it must never be able to fail a `money add`. The ledger is
    the product. A stale panel is a bad day; a refused expense is a deleted
    agent.

`upcoming_fixed` is tested hardest at the month boundary, because that is
where date arithmetic done by hand -- or by a model -- goes wrong: on the
29th, a bill on the 6th is eight days away and not minus twenty-three.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

TZ = "America/Sao_Paulo"


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    monkeypatch.setenv("CFO_DATA", str(tmp_path))
    monkeypatch.delenv("CFO_PANEL", raising=False)
    import importlib
    import money
    importlib.reload(money)
    import panel
    importlib.reload(panel)
    return money, panel


@pytest.fixture()
def con(mods):
    money, _ = mods
    c = money.connect()
    money.set_cfg(c, "timezone", TZ)
    money.set_cfg(c, "currency", "BRL")
    yield c
    c.close()


def at(day, hour=12, month=9):
    return datetime(2026, month, day, hour, 0, tzinfo=ZoneInfo(TZ))


def spend(money, con, cents, day, category="food", note="lunch"):
    """A transaction stamped on a chosen local day.

    add_tx() stamps `now`, and every figure on the panel is keyed by the
    owner's local day, so the tests write the day themselves rather than
    trying to move the clock.
    """
    tid = money.add_tx(con, cents, "expense", category, note, "test")
    con.execute("UPDATE tx SET day_local = ?, month_local = ? WHERE id = ?",
                (day.strftime("%Y-%m-%d"), day.strftime("%Y-%m"), tid))
    con.commit()
    return tid


# -- the projection the panel refuses to print -----------------------------

def test_no_projection_while_the_month_is_too_young(mods, con):
    """Two days in, the panel shows what is spent and nothing about a pace.

    This is the same refusal `cfo-brief` makes in prose. On a wall it matters
    more: the brief is read once, the panel sits there until midnight.
    """
    money, panel = mods
    money.set_cfg(con, "expected_income", "500000")
    money.main(["fixed", "add", "rent", "1800", "--day", "5"])
    spend(money, con, 4000, at(2))

    snap = panel.snapshot(con, today=at(2))
    assert snap["projection"] is None
    assert money.project_month(con, today=at(2))["basis"]["usable"] is False

    html = panel.render(snap)
    assert snap["words"]["too_young"] in html
    # And the figure that would have been the projection is nowhere on the
    # page -- not in a corner, not in a title attribute.
    assert money.fmt(money.project_month(con, today=at(2))["projected_expense"],
                     "BRL") not in html


def test_projection_appears_once_the_month_can_carry_one(mods, con):
    money, panel = mods
    money.set_cfg(con, "expected_income", "500000")
    money.main(["fixed", "add", "rent", "1800", "--day", "5"])
    for d in range(1, 11):
        spend(money, con, 3000, at(d))

    snap = panel.snapshot(con, today=at(10))
    assert snap["projection"] is not None
    assert snap["projection"]["expense_fmt"] in panel.render(snap)


# -- untrusted text ---------------------------------------------------------

def test_a_payee_out_of_a_statement_cannot_inject_markup(mods, con):
    """SOUL.md classes imported text as untrusted. In a browser it is code.

    The label goes in through `fixed add`, which is how a detected recurring
    line from an imported statement reaches the ledger -- the merchant name
    is whatever the bank printed.
    """
    money, panel = mods
    money.main(["fixed", "add", "<script>alert(1)</script>", "100", "--day", "3"])
    spend(money, con, 4000, at(2))

    html = panel.render(panel.snapshot(con, today=at(2)))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# -- what falls due, across a month boundary -------------------------------

def test_upcoming_looks_into_next_month_not_backwards(mods, con):
    """On 29 September, a bill on the 6th is seven days away.

    Read as a subtraction inside one month it is minus twenty-three, which
    sorts to the top of any list and reads as overdue. This is exactly the
    arithmetic SOUL.md forbids the model -- and the count crosses a thirty-day
    month, which is the second half of the same mistake.
    """
    money, _ = mods
    money.main(["fixed", "add", "condominio", "420", "--day", "6"])
    due = money.upcoming_fixed(con, today=at(29), within_days=10)
    assert [(d["label"], d["days_away"], d["due"]) for d in due] == [
        ("condominio", 7, "2026-10-06")]


def test_a_bill_on_the_31st_lands_on_the_30th_in_a_short_month(mods, con):
    """Clamped into the month, never dropped from it.

    September has thirty days. A rent line set to the 31st that simply did
    not appear would be a bill the panel silently omitted -- the one thing
    the card is for.
    """
    money, _ = mods
    money.main(["fixed", "add", "rent", "1800", "--day", "31"])
    due = money.upcoming_fixed(con, today=at(28), within_days=7)
    assert [(d["due"], d["days_away"]) for d in due] == [("2026-09-30", 2)]


def test_weekly_lines_are_left_out_rather_than_read_as_dates(mods, con):
    """`--every weekly --day 1` means Monday, not the 1st of the month.

    Reading that field as a date would put a confident, wrong line on the
    wall. Absent is correct; wrong is not.
    """
    money, _ = mods
    money.main(["fixed", "add", "faxina", "150", "--day", "1", "--every", "weekly"])
    assert money.upcoming_fixed(con, today=at(1), within_days=7) == []


def test_the_panel_lists_bills_only_not_the_salary(mods, con):
    """Income is real and due, but not under a heading that says "due"."""
    money, panel = mods
    money.main(["fixed", "add", "salario", "5000", "--kind", "income", "--day", "5"])
    money.main(["fixed", "add", "condominio", "420", "--day", "6"])
    spend(money, con, 4000, at(3))

    snap = panel.snapshot(con, today=at(3))
    assert [d["label"] for d in snap["upcoming"]] == ["condominio"]
    assert any(d["label"] == "salario"
               for d in money.upcoming_fixed(con, today=at(3)))


# -- the daily series -------------------------------------------------------

def test_daily_totals_keep_the_quiet_days_and_stop_at_today(mods, con):
    money, _ = mods
    spend(money, con, 1000, at(1))
    spend(money, con, 2500, at(3))

    days = money.daily_totals(con, "2026-09", today=at(3))
    assert [d["expense"] for d in days] == [1000, 0, 2500]


def test_a_finished_month_runs_to_its_last_day(mods, con):
    money, _ = mods
    spend(money, con, 1000, at(3, month=8))
    days = money.daily_totals(con, "2026-08", today=at(3))
    assert len(days) == 31
    assert days[2]["expense"] == 1000


# -- the panel may never break a ledger write ------------------------------

def test_a_broken_panel_does_not_fail_the_expense(mods, con, capsys, monkeypatch):
    """A path that cannot be written is a warning on stderr and nothing more.

    The failure this prevents: someone texts "gastei 40 no almoco", the
    ledger records it correctly, and the reply says it could not be recorded
    because a directory on the host was read-only.
    """
    money, _ = mods
    # A file where the panel wants a directory: mkdir raises, and the write
    # never gets as far as rendering.
    blocked = Path(os.environ["CFO_DATA"]) / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setenv("CFO_PANEL", str(blocked / "index.html"))

    assert money.main(["add", "40", "--note", "almoco"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["amount_cents"] == 4000     # stdout is still JSON
    assert "panel not refreshed" in out.err


def test_logging_a_spend_redraws_the_panel(mods, con):
    """The write hook, end to end: the figure is on the page, not in a queue."""
    money, panel = mods
    money.main(["add", "40", "--note", "almoco"])
    page = panel.panel_path(con).read_text()
    assert "R$ 40,00" in page


def test_the_panel_survives_an_empty_ledger(mods, con):
    """Rendered at install time, before anyone has logged anything.

    An exception here would land in the installer's output on a machine
    where nothing is wrong yet.
    """
    money, panel = mods
    snap = panel.snapshot(con, today=at(3))
    assert snap["is_empty"] is True
    html = panel.render(snap)
    assert snap["words"]["empty_hint"] in html


# -- the cron contract ------------------------------------------------------

def test_the_cron_script_prints_nothing_when_it_works(mods, tmp_path):
    """`--no-agent` delivers a script's stdout verbatim: "Empty stdout = silent".

    Anything printed here is a notification every ten minutes, forever. The
    path goes to stderr instead, where a person running it by hand sees it.
    """
    env = {**os.environ, "CFO_DATA": str(tmp_path), "PYTHONPATH": str(SCRIPTS)}
    env.pop("CFO_PANEL", None)
    res = subprocess.run([sys.executable, str(SCRIPTS / "panel.py")],
                         capture_output=True, text=True, env=env)
    assert res.returncode == 0
    assert res.stdout == ""
    assert "panel written to" in res.stderr
    assert (tmp_path / "panel" / "index.html").is_file()


def test_the_page_reaches_for_nothing_outside_the_machine(mods, con):
    """No CDN, no font, no tracker -- on a page holding someone's spending.

    It renders from `file://`, often with no network. An external stylesheet
    would be a request to somebody else's server on every refresh, carrying
    a referrer, for a document whose whole promise is that it stays here.
    """
    money, panel = mods
    spend(money, con, 4000, at(2))
    html = panel.render(panel.snapshot(con, today=at(2)))
    for reach in ("http://", "https://", "//cdn", "<script", "@import", "url("):
        assert reach not in html


def test_language_follows_the_setting_over_the_currency(mods, con):
    money, panel = mods
    assert panel.language(con) == "pt"           # inferred from BRL
    money.main(["config", "language", "en"])
    assert panel.language(con) == "en"
    money.set_cfg(con, "currency", "USD")
    money.set_cfg(con, "language", "")
    assert panel.language(con) == "en"           # inferred, no setting
