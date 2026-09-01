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


# -- basis: the projection's own honesty ------------------------------------

def test_basis_flags_an_empty_ledger_as_unusable(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4000, "expense", "food", when=day)
    b = mod.project_month(con, today=day)["basis"]
    assert b["usable"] is False
    assert any("income" in r for r in b["reasons"])


def test_simulate_carries_basis_so_a_verdict_can_be_withheld(mod, con):
    """With no income on file, projected income is zero and EVERYTHING is
    unaffordable -- including a coffee. The verdict is meaningless, and the
    caller has to be able to see that without inferring it."""
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 500, "expense", "food", when=day)
    s = mod.simulate(con, 300, today=day)          # R$3,00
    assert s["fits_this_month"] is False           # absurd on its face
    assert s["basis"]["usable"] is False           # and the data says why


def test_basis_becomes_usable_once_the_month_is_furnished(mod, con):
    day = datetime(2026, 6, 20, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    for d in range(1, 15):
        mod.add_tx(con, 3000, "expense", "food",
                   when=day.replace(day=d))
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('salary', 700000, 'income', 5)")
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('rent', 180000, 'expense', 5)")
    con.commit()
    b = mod.project_month(con, today=day)["basis"]
    assert b["usable"] is True and b["reasons"] == []


# -- naming the merchants the rules cannot ---------------------------------

def test_uncategorized_returns_distinct_payees_not_rows(mod, con):
    """The point is to hand a model a SHORT list: a hundred and fifty
    transactions become twenty names, deduplicated, worst first."""
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    for _ in range(8):
        mod.add_tx(con, 4000, "expense", "other", note="PADARIA DO ZE", when=day)
    for _ in range(2):
        mod.add_tx(con, 90000, "expense", "other", note="DUDA IMOVEIS", when=day)
    mod.add_tx(con, 5000, "expense", "food", note="already placed", when=day)

    out = mod.uncategorized(con)
    assert out["distinct"] == 2
    assert out["merchants"][0]["merchant"] == "DUDA IMOVEIS"   # by value
    assert out["merchants"][1]["count"] == 8
    assert all(m["merchant"] != "already placed" for m in out["merchants"])


def test_recategorize_applies_by_fragment(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    for _ in range(3):
        mod.add_tx(con, 4000, "expense", "other",
                   note="RAIA2116 FLORIANOPOLIS BRA #abc", when=day)

    out = mod.recategorize(con, {"RAIA2116": "health"})
    assert out["reclassified"] == 3
    assert mod.by_category(con, "2026-06")[0]["category"] == "health"


def test_recategorize_refuses_an_invented_category(mod, con):
    """A category outside the list would be invisible in every summary."""
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4000, "expense", "other", note="SOMEWHERE", when=day)

    out = mod.recategorize(con, {"SOMEWHERE": "vibes"})
    assert out["reclassified"] == 0
    assert out["rejected_categories"] == ["vibes"]


def test_recategorize_never_touches_what_was_already_placed(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4000, "expense", "groceries", note="MERCADO X", when=day)
    mod.recategorize(con, {"MERCADO X": "leisure"})
    assert mod.by_category(con, "2026-06")[0]["category"] == "groceries"


def test_recategorize_does_not_match_inside_a_longer_word(mod, con):
    """`LIKE '%raia%'` also matches PRAIA -- the parking lot becomes a
    pharmacy.

    This is the silent kind. The call returns `reclassified: 2` and reads like
    it worked; the money is simply filed under health from then on, and a
    summary showing health up by R$ 25,00 gives no hint why. Short merchant
    names are the normal case in a real statement, so the collision is not
    exotic -- Raia is a chain the model classified on the owner's first
    import, and praia is in half the addresses on a Santa Catarina statement.
    """
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 8990, "expense", "other", note="DROGA RAIA", when=day)
    mod.add_tx(con, 2500, "expense", "other",
               note="PRAIA GRANDE ESTACIONAMENTO", when=day)

    out = mod.recategorize(con, {"Raia": "health"})

    assert out["reclassified"] == 1
    by_cat = {c["category"]: c["total"] for c in mod.by_category(con, "2026-06")}
    assert by_cat["health"] == 8990       # the pharmacy, and only the pharmacy
    assert by_cat["other"] == 2500        # the parking lot, still honest


def test_recategorize_matches_a_name_split_across_words(mod, con):
    """The case the stricter matcher must not break, which is the common one.

    The map is written from the deduplicated payee, and that is rarely the
    whole note -- the note carries a transaction type, a branch, a city and a
    hash around it. Substring matching got this right; whole-word matching
    has to keep getting it right, or the fix above trades one silent wrong
    number for another.
    """
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4500, "expense", "other",
               note="Saida PIX AUTOPISTA LITORAL SUL S.A. #ab12", when=day)

    out = mod.recategorize(con, {"Autopista Litoral Sul": "transport"})
    assert out["reclassified"] == 1


def test_a_name_once_classified_is_not_asked_again(mod, con):
    """The forty names the model places are the asset, not the UPDATE.

    If the decision lives only in the rows present today, next month's
    statement arrives as the same forty unknowns and the owner watches the
    agent ask questions it already asked. Nothing errors -- the work is just
    silently thrown away every month.
    """
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 6000, "expense", "other", note="JERONIMO", when=day)

    out = mod.recategorize(con, {"Jeronimo": "food"})
    assert out["learned"] == 1

    learned = mod.learned_categories(con)
    assert mod.categorize_learned("JERONIMO BURGER HOUSE 04", learned) == "food"
    assert mod.categorize_learned("PADARIA DO ZE", learned) is None


def test_a_more_specific_name_wins_over_a_general_one(mod, con):
    """Both are true of the same note, so the order is the whole answer: a
    posto is transport, but this owner's Posto Ipiranga sells his groceries."""
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 1000, "expense", "other", note="POSTO IPIRANGA 42", when=day)
    mod.recategorize(con, {"Posto": "transport"})
    mod.recategorize(con, {"Posto Ipiranga": "groceries"})

    learned = mod.learned_categories(con)
    assert mod.categorize_learned("POSTO IPIRANGA 42", learned) == "groceries"
    assert mod.categorize_learned("POSTO SHELL BR101", learned) == "transport"


def test_a_learned_name_can_be_corrected_and_forgotten(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4000, "expense", "other", note="SESC FLORIPA", when=day)

    mod.recategorize(con, {"Sesc": "leisure"})
    mod.recategorize(con, {"SESC": "education"})    # same name, folded the same
    rows = con.execute("SELECT merchant, category FROM merchant_category").fetchall()
    assert len(rows) == 1 and rows[0]["category"] == "education"

    con.execute("DELETE FROM merchant_category WHERE merchant = ?",
                (" ".join(mod.merchant_tokens("sesc")),))
    assert mod.learned_categories(con) == []


def test_a_name_of_pure_punctuation_matches_nothing(mod, con):
    """It folds to zero words, and a zero-word name is contained in every
    note -- the whole ledger would land in one category."""
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 4000, "expense", "other", note="MERCADO X", when=day)
    mod.add_tx(con, 7000, "expense", "other", note="POSTO Y", when=day)

    out = mod.recategorize(con, {"--": "leisure"})
    assert out["reclassified"] == 0
    assert out["unusable_names"] == ["--"]
    assert mod.uncategorized(con)["distinct"] == 2


def test_accents_and_case_do_not_split_a_merchant(mod, con):
    day = datetime(2026, 6, 10, 12, 0, tzinfo=mod.ZoneInfo("America/Sao_Paulo"))
    mod.add_tx(con, 3000, "expense", "other", note="FARMACIA SAO JOAO", when=day)
    out = mod.recategorize(con, {"Farmácia São João": "health"})
    assert out["reclassified"] == 1


def test_a_single_day_can_be_sourced_without_adding_it_up(mod, con):
    """The brief's opening sentence had no command behind it.

    It is told to lead with what yesterday cost, and the one rule this agent
    has forbids it adding the rows itself. With nothing returning a day, it
    obeyed both and dropped the sentence every morning -- silently, and in a
    way that looked like a design choice rather than a missing feature.
    """
    tz = mod.ZoneInfo("America/Sao_Paulo")
    y = datetime(2026, 6, 10, 12, 0, tzinfo=tz)
    mod.add_tx(con, 4000, "expense", "food", note="ALMOCO", when=y)
    mod.add_tx(con, 2479, "expense", "groceries", note="MERCADO", when=y)
    mod.add_tx(con, 900000, "income", "other", note="SALARIO", when=y)
    mod.add_tx(con, 9999, "expense", "food", when=datetime(2026, 6, 11, 12, 0, tzinfo=tz))

    out = mod.day_totals(con, "2026-06-10")
    assert out["expense"] == 6479          # not the month, not the next day
    assert out["income"] == 900000
    assert out["count"] == 3
    assert out["categories"][0] == {"category": "food", "total": 4000, "n": 1}


def test_yesterday_is_the_owners_yesterday_not_the_utc_one(mod, con):
    """At 21:00 in Sao Paulo it is already tomorrow in UTC.

    Resolved in UTC, "yesterday" becomes today's date and the brief reports a
    day that has barely started -- most often as R$ 0,00, which reads like a
    quiet day rather than a bug. It is wrong for a third of every day, and
    always at the hours someone actually reads a message.
    """
    tz = mod.ZoneInfo("America/Sao_Paulo")
    evening = datetime(2026, 9, 1, 21, 0, tzinfo=tz)
    assert evening.astimezone(timezone.utc).strftime("%Y-%m-%d") == "2026-09-02"

    assert mod.yesterday_local(con, evening) == "2026-08-31"


def test_a_day_with_nothing_on_it_says_zero_rather_than_nothing(mod, con):
    out = mod.day_totals(con, "2026-06-10")
    assert out["expense"] == 0 and out["count"] == 0 and out["categories"] == []
