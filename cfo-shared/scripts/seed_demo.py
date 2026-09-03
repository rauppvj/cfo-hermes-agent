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
of reporting a comfortable month three times.
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

FIXED = [
    ("rent",     180000, "expense", 5),
    ("internet",  14990, "expense", 10),
    ("phone",      8990, "expense", 12),
    ("salary",   700000, "income",  5),
]

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

        for category, freq, typical, spread in PATTERN:
            for _ in range(int(round(freq * drift))):
                day = rng.randint(1, days)
                cents = max(100, int(rng.gauss(typical * drift, spread)))
                hour, minute = rng.randint(8, 22), rng.randrange(0, 60)
                if day > last_day:
                    continue  # drawn, then discarded: keeps the RNG stream stable
                when = (start + timedelta(days=day - 1)).replace(
                    hour=hour, minute=minute, tzinfo=tz)
                m.add_tx(con, cents, "expense", category,
                         note=rng.choice(NOTES.get(category, [""])),
                         source="demo", when=when)
                written += 1

    return {"transactions": written, "months_complete": months,
            "plus_current_month_through": today.strftime("%Y-%m-%d"),
            "seed": SEED}


def reset(con) -> dict:
    n = con.execute("DELETE FROM tx WHERE source = 'demo'").rowcount
    con.execute("DELETE FROM fixed WHERE label IN (%s)"
                % ",".join("?" * len(FIXED)), [f[0] for f in FIXED])
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
