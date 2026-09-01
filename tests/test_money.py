"""Tests for the ledger engine.

The ones that matter are the boundary tests: an amount read in the wrong
locale and a day resolved in the wrong zone are both silent -- they produce a
number, just not the right one, and nobody notices until the month closes
wrong. Everything else here is arithmetic that would fail loudly anyway.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"))


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("CFO_DATA", str(tmp_path))
    import money as m
    import importlib
    importlib.reload(m)
    c = m.connect()
    m.set_cfg(c, "timezone", "America/Sao_Paulo")
    m.set_cfg(c, "currency", "BRL")
    yield c
    c.close()


@pytest.fixture()
def mod(con):
    import money
    return money


# -- amounts ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,cents",
    [
        ("40", 4000),
        ("40.50", 4050),
        ("40,50", 4050),          # pt-BR decimal comma
        ("R$ 1.234,56", 123456),  # pt-BR grouping + decimal
        ("1,234.56", 123456),     # en-US grouping + decimal
        ("$99.99", 9999),
        ("0,99", 99),
        ("7000", 700000),
    ],
)
def test_parse_amount_reads_both_locales(mod, raw, cents):
    assert mod.parse_amount(raw) == cents


def test_parse_amount_rejects_garbage(mod):
    with pytest.raises(ValueError):
        mod.parse_amount("um pouco")
    with pytest.raises(ValueError):
        mod.parse_amount("")


def test_amounts_never_lose_a_cent_to_float(mod):
    # 0.1 + 0.2 in float is not 0.3; in cents it is exactly 30.
    assert mod.parse_amount("0,10") + mod.parse_amount("0,20") == 30


def test_fmt_is_locale_shaped(mod):
    assert mod.fmt(123456, "BRL") == "R$ 1.234,56"
    assert mod.fmt(123456, "USD") == "$1,234.56"


# -- the day boundary ------------------------------------------------------

def test_late_night_expense_stays_in_the_owners_month(mod, con):
    """A 22:00 expense on the last day of August in Sao Paulo is 01:00 on
    1 September UTC. Filed by UTC -- or by the container's Pacific clock --
    it lands in the wrong month and both months close wrong."""
    late = datetime(2026, 8, 31, 22, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4000, "expense", "food", when=late)

    row = con.execute("SELECT day_local, month_local, ts_utc FROM tx").fetchone()
    assert row["month_local"] == "2026-08"
    assert row["day_local"] == "2026-08-31"
    assert row["ts_utc"].startswith("2026-09-01")  # genuinely the next UTC day

    assert mod.month_totals(con, "2026-08")["expense"] == 4000
    assert mod.month_totals(con, "2026-09")["expense"] == 0


def test_owner_zone_beats_container_zone(mod, con, monkeypatch):
    """The container runs on the fleet default; the ledger must not."""
    monkeypatch.setenv("AGENT_TZ", "America/Los_Angeles")
    mod.set_cfg(con, "timezone", "America/Sao_Paulo")
    assert str(mod.tz_of(con)) == "America/Sao_Paulo"


def test_unknown_zone_falls_back_to_utc_not_to_the_container(mod, con):
    mod.set_cfg(con, "timezone", "Mars/Olympus_Mons")
    assert str(mod.tz_of(con)) == "UTC"


# -- projection ------------------------------------------------------------

def test_projection_extrapolates_pace_and_adds_fixed_whole(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    for _ in range(10):
        mod.add_tx(con, 1000, "expense", "food", when=day)  # R$100 over 10 days
    con.execute(
        "INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
        " VALUES ('rent', 180000, 'expense', 5)")
    con.commit()

    p = mod.project_month(con, today=day)
    assert p["elapsed_days"] == 10 and p["total_days"] == 30
    assert p["spent_so_far"] == 10000
    assert p["daily_rate"] == 1000
    assert p["projected_variable"] == 30000      # pace held for 30 days
    assert p["projected_expense"] == 30000 + 180000


def test_projection_on_day_one_does_not_divide_by_zero(mod, con):
    day = datetime(2026, 6, 1, 8, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    p = mod.project_month(con, today=day)
    assert p["spent_so_far"] == 0 and p["projected_expense"] == 0


# -- simulation ------------------------------------------------------------

def test_instalments_sum_back_to_the_price_exactly(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    s = mod.simulate(con, 200000, installments=3, today=day)
    total = s["first_installment"] + s["per_installment"] * 2
    assert total == 200000, "a split that loses a cent is a wrong answer"


def test_simulation_swing_equals_the_first_instalment(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 5000, "expense", "food", when=day)
    s = mod.simulate(con, 90000, installments=2, today=day)
    assert s["swing"] == s["first_installment"]
    assert s["projected_net_after"] == s["projected_net_before"] - s["swing"]


def test_simulation_reports_when_it_does_not_fit(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    con.execute(
        "INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
        " VALUES ('salary', 100000, 'income', 5)")
    con.commit()
    s = mod.simulate(con, 500000, installments=1, today=day)
    assert s["fits_this_month"] is False


def test_simulate_rejects_zero_instalments(mod, con):
    with pytest.raises(ValueError):
        mod.simulate(con, 1000, installments=0)
