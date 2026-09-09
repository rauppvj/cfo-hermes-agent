"""Tests for what the ledger learned on 2026-09-08.

That evening the owner listed four days of spending in one sitting, asked
how much money they had, and reported a balance the agent could not hold.
Every failure below was silent: a wrong day, a doubled card bill, a balance
that was really a net -- each a formatted, sourced, plausible number.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from test_money import con, mod  # noqa: F401  (fixtures)

SP = ZoneInfo("America/Sao_Paulo")


def at(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=SP)


# -- the day the owner named ---------------------------------------------------

def test_a_weekday_means_the_most_recent_one(mod, con):
    tuesday = at(2026, 9, 8, 22, 0)
    assert mod.resolve_on(con, "saturday", today=tuesday).date() == at(2026, 9, 5).date()
    assert mod.resolve_on(con, "sábado", today=tuesday).date() == at(2026, 9, 5).date()
    assert mod.resolve_on(con, "sab", today=tuesday).date() == at(2026, 9, 5).date()
    assert mod.resolve_on(con, "Monday", today=tuesday).date() == at(2026, 9, 7).date()
    # today included: "tuesday" said on a Tuesday is today
    assert mod.resolve_on(con, "tuesday", today=tuesday) == tuesday


def test_yesterday_and_anteontem(mod, con):
    tuesday = at(2026, 9, 8, 22, 0)
    assert mod.resolve_on(con, "ontem", today=tuesday).date() == at(2026, 9, 7).date()
    assert mod.resolve_on(con, "anteontem", today=tuesday).date() == at(2026, 9, 6).date()
    assert mod.resolve_on(con, "hoje", today=tuesday) == tuesday
    assert mod.resolve_on(con, None, today=tuesday) is None


def test_a_past_day_is_stamped_at_noon_in_the_owners_zone(mod, con):
    when = mod.resolve_on(con, "2026-09-05", today=at(2026, 9, 8))
    assert (when.hour, when.minute) == (12, 0)
    assert when.tzinfo == SP


def test_the_future_is_refused(mod, con):
    with pytest.raises(SystemExit):
        mod.resolve_on(con, "2026-09-09", today=at(2026, 9, 8))
    with pytest.raises(SystemExit):
        mod.resolve_on(con, "next week", today=at(2026, 9, 8))


def test_a_backdated_row_lands_on_its_own_day_and_month(mod, con):
    """Four days listed on the 8th used to become one day's spending, and
    the next brief opened with a 'yesterday' four times too big."""
    tuesday = at(2026, 9, 8, 22, 0)
    for day, cents in (("saturday", 100), ("sunday", 200), ("monday", 300)):
        mod.add_tx(con, cents, "expense", "food",
                   when=mod.resolve_on(con, day, today=tuesday))
    mod.add_tx(con, 400, "expense", "food", when=tuesday)
    assert mod.day_totals(con, "2026-09-05")["expense"] == 100
    assert mod.day_totals(con, "2026-09-06")["expense"] == 200
    assert mod.day_totals(con, "2026-09-07")["expense"] == 300
    assert mod.day_totals(con, "2026-09-08")["expense"] == 400
    # a Sunday on the 31st of August belongs to August, not to the month it
    # was typed in
    mod.add_tx(con, 500, "expense", "food",
               when=mod.resolve_on(con, "2026-08-31", today=tuesday))
    assert mod.month_totals(con, "2026-08")["expense"] == 500


# -- the payment method --------------------------------------------------------

@pytest.mark.parametrize("raw,method", [
    (None, "debit"), ("pix", "debit"), ("cash", "debit"), ("débito", "debit"),
    ("boleto", "debit"), ("card", "credit"), ("cartão", "credit"),
    ("credit", "credit"), ("cc", "credit"), ("Crédito", "credit"),
])
def test_methods_fold_to_debit_or_credit(mod, raw, method):
    assert mod.normalise_method(raw) == method


def test_an_unknown_method_is_refused_not_guessed(mod):
    with pytest.raises(SystemExit):
        mod.normalise_method("bitcoin")


def test_income_never_carries_a_credit_method(mod, con):
    tid = mod.add_tx(con, 1000, "income", "other", method="credit")
    assert mod.get_tx(con, tid)["method"] == "debit"


# -- the card bill is not spending ----------------------------------------------

def test_a_card_payment_moves_no_category_and_no_total(mod, con):
    """R$ 2.841,17 'paid my card bill' was filed as an expense on top of the
    purchases it settled, and the month doubled."""
    mod.add_tx(con, 5000, "expense", "shopping", method="credit")
    mod.add_tx(con, 3000, "expense", "food", method="credit")
    mod.pay_card(con, 8000)
    month = mod.now_local(con).strftime("%Y-%m")
    totals = mod.month_totals(con, month)
    assert totals["expense"] == 8000
    assert totals["card_paid"] == 8000
    assert totals["count"] == 2
    assert [c["category"] for c in mod.by_category(con, month)] == ["shopping", "food"]


def test_card_open_is_what_the_next_invoice_holds(mod, con):
    mod.add_tx(con, 5000, "expense", "shopping", method="credit")
    mod.add_tx(con, 700, "expense", "food", method="debit")
    assert mod.card_open(con)["card_open_cents"] == 5000
    mod.pay_card(con, 5000)
    assert mod.card_open(con)["card_open_cents"] == 0
    mod.add_tx(con, 1200, "expense", "leisure", method="credit")
    assert mod.card_open(con)["card_open_cents"] == 1200


# -- the balance ---------------------------------------------------------------

def test_without_an_anchor_the_balance_says_what_to_ask(mod, con):
    mod.add_tx(con, 900000, "income", "other")
    mod.add_tx(con, 5000, "expense", "food")
    b = mod.balance(con)
    assert b["has_anchor"] is False
    assert b["balance_cents"] is None
    assert b["basis"]["usable"] is False
    assert "balance set" in b["basis"]["reasons"][0]


def test_the_balance_is_the_reading_plus_what_moved_the_account(mod, con):
    """Debit spending and card payments leave the account; a card purchase
    does not until the invoice is paid; income comes in."""
    mod.set_balance(con, 131240)
    mod.add_tx(con, 5000, "expense", "food", method="debit")
    mod.add_tx(con, 6000, "expense", "shopping", method="credit")
    mod.add_tx(con, 20000, "income", "other")
    b = mod.balance(con)
    assert b["balance_cents"] == 131240 - 5000 + 20000
    assert b["card_open_cents"] == 6000
    mod.pay_card(con, 6000)
    b = mod.balance(con)
    assert b["balance_cents"] == 131240 - 5000 + 20000 - 6000
    assert b["card_open_cents"] == 0


def test_a_row_in_the_same_second_as_the_anchor_still_counts(mod, con):
    """The agent runs `balance set` and `add` in one breath. Timestamps are
    whole seconds, so the order has to come from the id."""
    mod.set_balance(con, 100000)
    mod.add_tx(con, 2500, "expense", "food")
    assert mod.balance(con)["balance_cents"] == 97500


def test_a_row_backdated_to_before_the_reading_stays_out(mod, con):
    """'Saturday I also paid 30', said on Tuesday, was already in the figure
    the owner read off the bank on Monday night."""
    monday = at(2026, 9, 7, 22, 34)
    mod.set_balance(con, 100000, when=monday)
    mod.add_tx(con, 3000, "expense", "food",
               when=mod.resolve_on(con, "saturday", today=at(2026, 9, 8, 10)))
    assert mod.balance(con, today=at(2026, 9, 8, 10))["balance_cents"] == 100000


def test_a_new_reading_replaces_the_old_one(mod, con):
    mod.set_balance(con, 100000)
    mod.add_tx(con, 5000, "expense", "food")
    mod.set_balance(con, 50000)
    assert mod.balance(con)["balance_cents"] == 50000
    assert mod.balance(con)["income_since_cents"] == 0


def test_an_old_reading_is_flagged_not_refused(mod, con):
    mod.set_balance(con, 100000, when=at(2026, 8, 1))
    b = mod.balance(con, today=at(2026, 9, 8))
    assert b["basis"]["usable"] is True
    assert b["basis"]["stale"] is True
    assert "days old" in b["basis"]["reasons"][0]


def test_the_balance_says_what_is_left_after_this_weeks_bills(mod, con):
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('rent', 150000, 'expense', 10)")
    con.commit()
    mod.set_balance(con, 200000, when=at(2026, 9, 8))
    b = mod.balance(con, today=at(2026, 9, 8))
    assert b["due_soon_cents"] == 150000
    assert b["after_due_soon_cents"] == 50000


def test_status_asks_for_a_balance_once_income_and_bills_are_known(mod, con):
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('salary', 700000, 'income', 5)")
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('rent', 150000, 'expense', 10)")
    con.commit()
    assert "balance set" in mod.status(con)["next_step"]
    mod.set_balance(con, 100000)
    assert "balance" not in (mod.status(con)["next_step"] or "")


# -- budgets -------------------------------------------------------------------

def test_a_budget_reports_share_and_attention(mod, con):
    mod.set_budget(con, "food", 10000)
    today = at(2026, 9, 12)
    mod.add_tx(con, 8500, "expense", "food", when=today)
    b = mod.budgets(con, today=today)
    row = b["budgets"][0]
    assert (row["pct"], row["left_cents"], row["over"]) == (85, 1500, False)
    assert b["attention"] == ["food"]
    mod.add_tx(con, 2000, "expense", "food", when=today)
    row = mod.budgets(con, today=today)["budgets"][0]
    assert row["over"] is True and row["left_cents"] == -500


def test_a_budget_pace_is_withheld_while_the_month_is_young(mod, con):
    mod.set_budget(con, "food", 10000)
    mod.add_tx(con, 500, "expense", "food", when=at(2026, 9, 2))
    assert mod.budgets(con, today=at(2026, 9, 2))["budgets"][0]["on_pace_to_pass"] is None
    # 500 in 2 days is 7,500 a month: under; by the 12th it is a pace
    mod.add_tx(con, 4000, "expense", "food", when=at(2026, 9, 11))
    row = mod.budgets(con, today=at(2026, 9, 12))["budgets"][0]
    assert row["projected_cents"] == round(4500 / 12 * 30)
    assert row["on_pace_to_pass"] is True


def test_a_budget_needs_a_real_category(mod, con):
    with pytest.raises(SystemExit):
        mod.set_budget(con, "crypto", 100)


# -- the week ------------------------------------------------------------------

def test_the_week_compares_the_same_days_not_a_whole_week(mod, con):
    wed = at(2026, 9, 9, 15)             # Wednesday
    for d, cents in ((7, 100), (8, 100), (9, 100)):          # this Mon-Wed
        mod.add_tx(con, cents, "expense", "food", when=at(2026, 9, d))
    for d, cents in ((31, 50), (1, 50), (2, 50), (5, 900)):  # last Mon-Wed + Sat
        mod.add_tx(con, cents, "expense", "food",
                   when=at(2026, 8 if d == 31 else 9, d))
    w = mod.week(con, today=wed)
    assert w["week_start"] == "2026-09-07"
    assert w["days_elapsed"] == 3
    assert w["this_week_cents"] == 300
    assert w["last_week_same_days_cents"] == 150
    assert w["delta_cents"] == 150
    assert w["last_week_full_cents"] == 1050


# -- edit and undo -------------------------------------------------------------

def test_edit_moves_a_row_and_keeps_its_id(mod, con):
    tid = mod.add_tx(con, 4000, "expense", "food", when=at(2026, 9, 8))
    out = mod.edit_tx(con, tid, when=at(2026, 9, 5, 12), amount_cents=4500)
    row = mod.get_tx(con, tid)
    assert row["day_local"] == "2026-09-05" and row["amount_cents"] == 4500
    assert set(out["changed"]) == {"ts_utc", "day_local", "amount_cents"}
    assert out["before"]["day_local"] == "2026-09-08"


def test_edit_can_turn_a_misfiled_expense_into_a_card_payment(mod, con):
    tid = mod.add_tx(con, 284117, "expense", "other", note="paid card bill")
    mod.edit_tx(con, tid, kind="transfer", category=mod.CARD_PAYMENT)
    month = mod.now_local(con).strftime("%Y-%m")
    assert mod.month_totals(con, month)["expense"] == 0
    assert mod.month_totals(con, month)["card_paid"] == 284117


def test_undo_takes_the_last_chat_row_and_never_an_import_or_demo_row(mod, con):
    mod.add_tx(con, 100, "expense", "food", source="chat")
    mod.add_tx(con, 200, "expense", "food", source="demo")
    mod.add_tx(con, 300, "expense", "food", source="import:20260901")
    last = mod.last_logged(con)
    assert last["amount_cents"] == 100
    con.execute("DELETE FROM tx")
    con.commit()
    assert mod.last_logged(con) is None


# -- export --------------------------------------------------------------------

def test_export_writes_every_row_of_the_month_as_csv(mod, con, tmp_path):
    mod.add_tx(con, 4050, "expense", "food", note="lunch", when=at(2026, 9, 3))
    mod.add_tx(con, 1000, "expense", "food", when=at(2026, 8, 3))
    out = mod.export_csv(con, "2026-09")
    text = Path(out["path"]).read_text()
    assert out["rows"] == 1
    assert "40.50" in text and "lunch" in text and "2026-08" not in text
    assert out["send"].startswith("MEDIA:")


# -- language ------------------------------------------------------------------

@pytest.mark.parametrize("raw,code", [
    ("pt-BR", "pt"), ("Portuguese", "pt"), ("en_US", "en"), ("EN", "en"),
    ("español", "es"),
])
def test_language_folds_to_two_letters(mod, raw, code):
    assert mod.validate_cfg("language", raw) == code


def test_language_falls_back_to_the_currency(mod, con):
    assert mod.language_of(con) == "pt"          # BRL
    mod.set_cfg(con, "currency", "USD")
    assert mod.language_of(con) == "en"
    mod.set_cfg(con, "language", "pt")
    assert mod.language_of(con) == "pt"


# -- the migration -------------------------------------------------------------

def test_a_v3_ledger_opens_as_v4_with_every_row_and_total_intact(mod, tmp_path, monkeypatch):
    """A real person's ledger is rebuilt in place: same ids, same totals,
    every old row `debit`, and a backup beside it."""
    data = tmp_path / "v3"
    data.mkdir()
    db = sqlite3.connect(data / "ledger.db")
    db.executescript("""
        CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO config VALUES ('schema_version', '3'), ('timezone', 'UTC');
        CREATE TABLE tx (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL,
            day_local TEXT NOT NULL, month_local TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            kind TEXT NOT NULL CHECK (kind IN ('expense','income')),
            category TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat', created_utc TEXT NOT NULL);
        INSERT INTO tx (id, ts_utc, day_local, month_local, amount_cents, kind, category, note, source, created_utc)
        VALUES (7, '2026-09-01T12:00:00+00:00', '2026-09-01', '2026-09', 4000, 'expense', 'food', 'lunch', 'chat', 'x'),
               (9, '2026-09-02T12:00:00+00:00', '2026-09-02', '2026-09', 700000, 'income', 'other', 'pay', 'chat', 'x');
        CREATE TABLE fixed (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
            amount_cents INTEGER NOT NULL, kind TEXT NOT NULL, day_of_month INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1, frequency TEXT NOT NULL DEFAULT 'monthly');
        CREATE TABLE merchant_category (merchant TEXT PRIMARY KEY, label TEXT NOT NULL,
            category TEXT NOT NULL, updated_utc TEXT NOT NULL);
    """)
    db.commit()
    db.close()

    monkeypatch.setenv("CFO_DATA", str(data))
    c = mod.connect()
    assert mod.get_cfg(c, "schema_version") == "4"
    rows = c.execute("SELECT id, method, kind FROM tx ORDER BY id").fetchall()
    assert [(r["id"], r["method"]) for r in rows] == [(7, "debit"), (9, "debit")]
    assert mod.month_totals(c, "2026-09") == {
        "month": "2026-09", "expense": 4000, "income": 700000, "count": 2,
        "card_paid": 0, "net": 696000}
    assert (data / "ledger.db.v3.bak").exists()
    # the new kind is accepted, the next id continues the sequence
    tid = mod.pay_card(c, 100)
    assert tid == 10
    # and opening it again is a no-op
    c.close()
    c = mod.connect()
    assert c.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 3
    c.close()


# -- named cards ---------------------------------------------------------------

def test_a_wallet_tap_is_credit_unless_the_card_was_named_debit(mod, con):
    assert mod.method_of_card(con, "Nubank") == "credit"
    mod.set_card(con, "Inter", "debit")
    assert mod.method_of_card(con, "inter") == "debit"
    assert mod.method_of_card(con, "Banco Inter") == "credit"   # a different name
    assert mod.cards(con) == [{"card": "inter", "method": "debit"}]
    with pytest.raises(SystemExit):
        mod.set_card(con, "---", "debit")


def test_simulate_says_what_the_account_would_hold_after_the_first_instalment(mod, con):
    for d in range(1, 8):
        mod.add_tx(con, 1000, "expense", "food", when=at(2026, 9, d))
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('salary', 700000, 'income', 5)")
    con.execute("INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES ('rent', 150000, 'expense', 10)")
    con.commit()
    s = mod.simulate(con, 120000, installments=3, today=at(2026, 9, 8))
    assert s["balance_now_cents"] is None
    mod.set_balance(con, 100000, when=at(2026, 9, 8, 9))
    s = mod.simulate(con, 120000, installments=3, today=at(2026, 9, 8, 10))
    assert s["balance_now_cents"] == 100000
    assert s["balance_after_first_cents"] == 100000 - 40000


# -- one purchase, two channels ---------------------------------------------------

def test_a_tap_and_the_banks_sms_for_the_same_charge_are_one_row(mod, con):
    mod.add_tx(con, 1490, "expense", "food", source="wallet", note="Padaria")
    assert mod.forwarded_twin(con, 1490, "sms")["source"] == "wallet"
    assert mod.forwarded_twin(con, 1490, "wallet") is None       # same channel: kept
    assert mod.forwarded_twin(con, 1490, "chat") is None         # typed: never deduped
    assert mod.forwarded_twin(con, 1500, "sms") is None          # a different amount
    # a twin from twenty-one minutes ago is a different purchase
    con.execute("UPDATE tx SET created_utc = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=21)).isoformat(timespec="seconds"),))
    con.commit()
    assert mod.forwarded_twin(con, 1490, "sms") is None


def test_the_cli_skips_the_twin_and_says_so(mod, con, capsys, monkeypatch):
    import money as m
    m.main(["add", "14,90", "--category", "food", "--note", "Padaria", "--source", "wallet"])
    capsys.readouterr()
    m.main(["add", "14,90", "--category", "food", "--note", "Compra aprovada", "--source", "sms"])
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True and out["already_logged_from"] == "wallet"
    assert con.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 1
