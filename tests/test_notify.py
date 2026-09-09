"""The push channel, end to end on fakes: the Mac watcher over a fake
Notification Center database, the container gate over the inbox it writes,
and the one rule that joins the channels -- the bank's text corrects the
tap's method instead of duplicating the purchase."""

import json
import plistlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_money import con, mod  # noqa: F401

SCRIPTS = Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))


# -- record_forwarded: one purchase, three channels, the bank decides the method

def test_the_banks_alert_corrects_the_taps_method_instead_of_doubling(mod, con):
    tap = mod.record_forwarded(con, 4590, "expense", "food", "PADARIA", "wallet")
    assert tap["skipped"] is False and tap["method"] == "credit"
    push = mod.record_forwarded(con, 4590, "expense", "food", "PADARIA CENTRAL", "push",
                                method="debit")
    assert push["skipped"] is True and push["duplicate_of"] == tap["id"]
    assert push["method_updated"] is True and push["method"] == "debit"
    assert mod.get_tx(con, tap["id"])["method"] == "debit"
    assert con.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 1


def test_an_alert_that_says_nothing_about_the_method_leaves_the_tap_alone(mod, con):
    tap = mod.record_forwarded(con, 4590, "expense", "food", "PADARIA", "wallet")
    sms = mod.record_forwarded(con, 4590, "expense", "food", "PADARIA", "sms")
    assert sms["skipped"] is True and sms["method_updated"] is False
    assert mod.get_tx(con, tap["id"])["method"] == "credit"


def test_a_named_debit_card_makes_the_tap_debit(mod, con):
    mod.set_card(con, "Inter", "debit")
    got = mod.record_forwarded(con, 1000, "expense", "food", "x", "wallet", card="Inter")
    assert got["method"] == "debit"


def test_income_forwarded_is_never_deduplicated_against_spending(mod, con):
    mod.record_forwarded(con, 26015, "expense", "other", "x", "wallet")
    got = mod.record_forwarded(con, 26015, "income", "other", "FULANO", "push", method="debit")
    assert got["skipped"] is False and got["kind"] == "income"


# -- the container gate ---------------------------------------------------------

@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "inbox").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(h))
    return h


def _line(body, app="com.nu.production", title="Compra aprovada"):
    return json.dumps({"ts": "2026-09-09T17:00:00+00:00", "app": app,
                       "title": title, "subtitle": "", "body": body}) + "\n"


def test_the_gate_logs_what_carries_an_amount_and_wakes_the_agent_once(mod, con, home, capsys):
    import importlib
    import notify_gate
    importlib.reload(notify_gate)
    inbox = home / "inbox" / "notifications.jsonl"
    inbox.write_text(
        _line("Compra no débito aprovada: R$ 45,90 em PADARIA CENTRAL")
        + _line("Compra negada: R$ 99,00 em LOJA X")
        + _line("Seu código é 1234", title="Acesso"))
    mod.set_cfg(con, "language", "en")

    assert notify_gate.main() == 0
    out = capsys.readouterr().out
    assert "✓ logged" in out and "PADARIA CENTRAL" in out and "food" in out and "debit" in out
    assert "ℹ not logged (declined)" in out
    assert "? no amount" in out
    assert "Write one line, in English" in out
    rows = con.execute("SELECT amount_cents, method, category, source FROM tx").fetchall()
    assert [tuple(r) for r in rows] == [(4590, "debit", "food", "push")]

    # nothing new: the gate stays closed and costs nothing
    assert notify_gate.main() == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"wakeAgent": False}

    # a purchase already tapped is corrected, not doubled
    mod.record_forwarded(con, 12000, "expense", "shopping", "LOJA", "wallet")
    with inbox.open("a") as fh:
        fh.write(_line("Compra no débito aprovada: R$ 120,00 em LOJA DE MOVEIS"))
    notify_gate.main()
    out = capsys.readouterr().out
    assert "already logged from the wallet" in out and "corrected to debit" in out
    assert con.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 2


def test_the_gate_never_wakes_the_agent_when_there_is_no_inbox(mod, con, home, capsys):
    import importlib
    import notify_gate
    importlib.reload(notify_gate)
    assert notify_gate.main() == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"wakeAgent": False}


# -- the Mac watcher, over a fake Notification Center -----------------------------

def _fake_center(path: Path, rows):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier TEXT);
        CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER,
                             data BLOB, delivered_date REAL);
        INSERT INTO app VALUES (1, 'com.nu.production'), (2, 'com.apple.MobileSMS');
    """)
    for i, (app_id, title, body) in enumerate(rows, start=1):
        blob = plistlib.dumps({"req": {"titl": title, "body": body}})
        db.execute("INSERT INTO record VALUES (?,?,?,?)", (i, app_id, blob, 780000000.0 + i))
    db.commit()
    db.close()


def test_the_watcher_forwards_only_money_and_only_what_is_new(tmp_path):
    import notify_watch
    db = tmp_path / "db"
    home = tmp_path / "home"
    _fake_center(db, [
        (1, "Compra aprovada", "Compra no débito de R$ 45,90 em PADARIA CENTRAL"),
        (2, "Alice", "chegando em 10 min"),
    ])
    # first run: starts from now, forwards nothing old
    first = notify_watch.scan(db, home)
    assert first["kept"] == 0 and first["last_rec_id"] == 2
    assert not (home / "inbox" / "notifications.jsonl").exists() or \
        (home / "inbox" / "notifications.jsonl").read_text() == ""

    # new notifications arrive
    con = sqlite3.connect(db)
    con.execute("INSERT INTO record VALUES (3, 1, ?, 780000003.0)",
                (plistlib.dumps({"req": {"titl": "Compra aprovada",
                                          "body": "Compra de R$ 89,90 em LOJA ONLINE aprovada"}}),))
    con.execute("INSERT INTO record VALUES (4, 2, ?, 780000004.0)",
                (plistlib.dumps({"req": {"titl": "Bob", "body": "bora?"}}),))
    con.commit()
    con.close()
    second = notify_watch.scan(db, home)
    assert second["seen"] == 2 and second["kept"] == 1
    lines = [json.loads(l) for l in (home / "inbox" / "notifications.jsonl").read_text().splitlines()]
    assert lines[0]["app"] == "com.nu.production" and "LOJA ONLINE" in lines[0]["body"]
    assert lines[0]["ts"].startswith("2025-")            # cocoa seconds, not unix
    # and nothing is read twice
    assert notify_watch.scan(db, home)["seen"] == 0


def test_the_watcher_can_look_back_once(tmp_path):
    import notify_watch
    db = tmp_path / "db"
    _fake_center(db, [(1, "Compra aprovada", "R$ 10,00 em A"),
                      (1, "Compra aprovada", "R$ 20,00 em B")])
    got = notify_watch.scan(db, tmp_path / "home", backfill=1)
    assert got["kept"] == 1
    assert "em B" in (tmp_path / "home" / "inbox" / "notifications.jsonl").read_text()


def test_an_unreadable_blob_is_an_empty_notification_not_a_crash():
    import notify_watch
    assert notify_watch.unpack(b"not a plist") == {"title": "", "subtitle": "", "body": ""}
