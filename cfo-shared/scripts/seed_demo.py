#!/usr/bin/env python3
"""Seed a demo ledger so a fresh install has something to talk about.

Why this exists: a budgeting agent's cold start is an empty database. Someone
who clones this repo, texts it "how am I doing?" and gets "no data yet" has
learned nothing about whether the agent is any good. Three months of
plausible history means the first question already has a real answer.

Two properties matter more than realism:

  * DETERMINISTIC PER MONTH. Each month's rows come from an RNG seeded by
    that month's own offset, so a given month holds the same transactions no
    matter which day you seed it. The completed months are therefore
    byte-identical on every machine.
  * ANCHORED TO TODAY, NOT TO A DATE IN THE PAST. The months are counted back
    from the CURRENT month, and the current month is filled in up to today.
    An anchor frozen at a release date looks fine until the calendar passes
    it: seed on 5 September against a 1 September anchor and the current
    month is empty, so the first question anyone asks -- "how is my month
    going?" -- answers "no data", which is the exact cold start this file
    exists to prevent.
  * HONEST. The seeded rows carry source='demo', so `money` can tell them
    apart from a real person's spending and nothing here can be mistaken for
    it. `--reset` drops them and leaves real rows alone.

The story in the data is deliberate: variable spending creeps up ~12% a month
while income is flat, so the projection has something to warn about instead
of reporting a comfortable month three times. Shopping, leisure and the
subscriptions go on the card and the card is paid on the 10th, so the balance
and the open invoice show the difference between "spent" and "left the
account"; food has a budget it is brushing against, so the brief and the
panel have a ceiling to point at.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import money as m  # noqa: E402

SEED = 20260908

# (category, weekday-ish frequency per month, typical cents, spread)
PATTERN = [
    ("food",          18, 3800,  1800),
    ("groceries",      5, 21000, 9000),
    ("transport",     12, 1900,  1200),
    ("leisure",        4, 8500,  5000),
    ("health",         1, 14000, 6000),
    ("shopping",       3, 16000, 12000),
    ("subscriptions",  2, 3990,  1500),
]

# What goes on the card. Everything else leaves the account as it is spent.
ON_CARD = {"leisure", "shopping", "subscriptions"}
CARD_PAY_DAY = 10

# Spread across the month on purpose. The panel's "due within 7 days" card
# and the brief's "after this week's bills" line are windows onto TODAY, so a
# sample whose bills all fall on the 5th and the 10th shows an empty card to
# anyone who seeds it after the 12th -- which was every screenshot taken on
# 2026-09-12. Five dates, five days apart, means any day of the month has two
# or three bills in view.
FIXED = [
    ("rent",        180000, "expense", 5),
    ("internet",     14990, "expense", 10),
    ("phone",         8990, "expense", 12),
    ("electricity",  21050, "expense", 16),
    ("gym",           9900, "expense", 20),
    ("health plan",  34820, "expense", 25),
    ("salary",      700000, "income",  5),
]

# A ceiling per category, in cents. Food sits near its line by design.
BUDGETS = [("food", 100000), ("leisure", 40000), ("shopping", 50000)]

# What the account held on the 1st of the current month. A round figure
# anyone can recognise as invented, so the panel and the brief have a balance
# to say without pretending the sample knows anyone's bank.
OPENING_BALANCE = 650000

NOTES = {
    "food": ["lunch", "coffee", "dinner out", "bakery", "delivery"],
    "groceries": ["supermarket", "market run"],
    "transport": ["ride", "fuel", "parking", "bus"],
    "leisure": ["cinema", "bar with friends", "concert"],
    "health": ["pharmacy", "dentist"],
    "shopping": ["clothes", "household", "gift"],
    "subscriptions": ["streaming", "music", "cloud storage"],
}


def month_start(anchor: datetime, back: int) -> datetime:
    d = anchor.replace(day=1)
    for _ in range(back):
        d = (d - timedelta(days=1)).replace(day=1)
    return d


def next_month(d: datetime) -> datetime:
    return (d.replace(day=28) + timedelta(days=8)).replace(day=1)


def seed(con, months: int = 3) -> dict:
    """Seed `months` completed months plus the current one, up to today."""
    tz = m.tz_of(con)
    today = m.now_local(con)
    this_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    written = 0

    for f_label, f_cents, f_kind, f_day in FIXED:
        exists = con.execute(
            "SELECT 1 FROM fixed WHERE label = ?", (f_label,)).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO fixed (label, amount_cents, kind, day_of_month)"
                " VALUES (?,?,?,?)", (f_label, f_cents, f_kind, f_day))
    con.commit()

    # `back` counts down to 0, which is the current, partial month.
    card_carried = 0        # what last month put on the card, paid on the 10th
    for back in range(months, -1, -1):
        start = month_start(this_month, back)
        days = (next_month(start) - start).days
        # Only the days that have actually happened. A ledger that already
        # holds next week's spending is a ledger nobody believes.
        last_day = today.day if back == 0 else days
        if last_day < 1:
            continue

        # Seeded per month offset, so a month's rows do not change when the
        # calendar moves under them.
        rng = random.Random(SEED + back)
        drift = 1.0 + 0.12 * (months - back)

        if card_carried and CARD_PAY_DAY <= last_day:
            when = (start + timedelta(days=CARD_PAY_DAY - 1)).replace(
                hour=9, minute=5, tzinfo=tz)
            m.add_tx(con, card_carried, "transfer", m.CARD_PAYMENT,
                     note="card invoice", source="demo", when=when)
            written += 1
        on_card_this_month = 0

        for category, freq, typical, spread in PATTERN:
            for _ in range(int(round(freq * drift))):
                day = rng.randint(1, days)
                cents = max(100, int(rng.gauss(typical * drift, spread)))
                hour, minute = rng.randint(8, 22), rng.randrange(0, 60)
                if day > last_day:
                    continue  # drawn, then discarded: keeps the RNG stream stable
                when = (start + timedelta(days=day - 1)).replace(
                    hour=hour, minute=minute, tzinfo=tz)
                method = "credit" if category in ON_CARD else "debit"
                m.add_tx(con, cents, "expense", category,
                         note=rng.choice(NOTES.get(category, [""])),
                         source="demo", when=when, method=method)
                written += 1
                if method == "credit":
                    on_card_this_month += cents
        card_carried = on_card_this_month

    for category, cents in BUDGETS:
        if not con.execute("SELECT 1 FROM budget WHERE category = ?",
                           (category,)).fetchone():
            m.set_budget(con, category, cents)

    # The account on the 1st, so the balance moves with this month's rows.
    # Only when no real reading exists: the owner's figure always wins.
    if not m.balance_anchor(con):
        m.set_balance(con, OPENING_BALANCE, when=this_month.replace(hour=0, minute=1, tzinfo=tz))
        m.set_cfg(con, "demo_balance", "1")

    return {"transactions": written, "months_complete": months,
            "plus_current_month_through": today.strftime("%Y-%m-%d"),
            "budgets": [c for c, _ in BUDGETS],
            "opening_balance_cents": OPENING_BALANCE,
            "seed": SEED}


def reset(con) -> dict:
    n = con.execute("DELETE FROM tx WHERE source = 'demo'").rowcount
    con.execute("DELETE FROM fixed WHERE label IN (%s)"
                % ",".join("?" * len(FIXED)), [f[0] for f in FIXED])
    for category, cents in BUDGETS:
        # Only the ceilings this file wrote, at the values it wrote them.
        con.execute("DELETE FROM budget WHERE category = ? AND amount_cents = ?",
                    (category, cents))
    if m.get_cfg(con, "demo_balance"):
        con.execute("DELETE FROM config WHERE key IN ('balance_anchor', 'demo_balance')")
    con.commit()
    return {"removed": n}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="seed or clear the demo ledger")
    p.add_argument("--months", type=int, default=3)
    p.add_argument("--reset", action="store_true",
                   help="remove demo rows only; real transactions are untouched")
    args = p.parse_args(argv)

    con = m.connect()
    if not m.get_cfg(con, "timezone"):
        m.set_cfg(con, "timezone", "America/Sao_Paulo")
    if not m.get_cfg(con, "currency"):
        m.set_cfg(con, "currency", "BRL")

    result = reset(con) if args.reset else seed(con, args.months)
    m.emit(result, m.currency_of(con))
    # So the panel exists before anyone has logged anything real -- the demo
    # ledger is what the README's one-minute try renders, and a blank page is
    # a worse answer than a sample clearly marked as one.
    m.refresh_panel(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
