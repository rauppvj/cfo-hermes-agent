#!/usr/bin/env python3
"""The ledger engine. Every number the agent says comes from here.

The split this file exists to enforce: the MODEL READS, THE CODE CALCULATES.
Hermes turns "gastei 40 no almoco" into an `add` call; it never does the
arithmetic itself and never guesses a total. A language model that adds a
column of numbers is a model that will eventually add them wrong, quietly, in
someone's budget -- so nothing here takes a number from prose.

Two invariants hold the rest up:

  * Money is integer cents. No float ever touches an amount.
  * A transaction's day is the OWNER's day. The container's clock is
    America/Los_Angeles by fleet default (agent-mgr) while the owner may be
    anywhere; a 22:00 expense in Sao Paulo is 18:00 the same day in Los
    Angeles, but a 21:00 one is 17:00 -- and on the last day of a month that
    shifts the expense into the wrong month, which is the one error a
    budgeting tool may not make. Every row therefore stores both the UTC
    instant and the local calendar day, resolved once at write time against
    the configured zone.

State lives in $CFO_DATA (the instance home, mounted at /opt/data), never in
this repo -- the repo is code only and carries nobody's data.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 2

DEFAULT_CATEGORIES = [
    "food", "groceries", "transport", "housing", "utilities", "health",
    "education", "shopping", "leisure", "subscriptions", "fees", "other",
]


def data_dir() -> Path:
    return Path(os.environ.get("CFO_DATA", "/opt/data/cfo"))


def db_path() -> Path:
    return data_dir() / "ledger.db"


# --------------------------------------------------------------------------
# money: integer cents in, formatted strings out
# --------------------------------------------------------------------------

def parse_amount(raw: str) -> int:
    """Parse a human amount into cents.

    Accepts "40", "40.50", "40,50", "R$ 40,50", "1.234,56", "1,234.56".
    Rejects anything it cannot read exactly -- a budgeting tool that guesses
    at an ambiguous amount is worse than one that asks.
    """
    s = str(raw).strip()
    for junk in ("R$", "r$", "$", "US$", "BRL", "USD", "EUR", "€", "£"):
        s = s.replace(junk, "")
    s = s.replace(" ", "").replace(" ", "")
    if not s:
        raise ValueError("empty amount")
    neg = s.startswith("-")
    s = s.lstrip("+-")

    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        # The rightmost separator is the decimal one: "1.234,56" / "1,234.56".
        sep = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("." if sep == "," else ",", "")
        s = s.replace(sep, ".")
    elif has_comma:
        # "40,50" is decimal; "1,234" is a thousands group.
        s = s.replace(",", ".") if len(s.split(",")[-1]) != 3 else s.replace(",", "")
    elif has_dot and len(s.split(".")[-1]) == 3 and s.count(".") >= 1 and len(s.split(".")[0]) <= 3:
        # "1.234" -- ambiguous, but in a thousands-grouping locale it is 1234.
        # Only when there is no other decimal evidence.
        s = s.replace(".", "")

    try:
        cents = int(round(float(s) * 100))
    except ValueError as exc:
        raise ValueError(f"cannot read amount: {raw!r}") from exc
    if cents < 0:
        raise ValueError("amount must be positive; use --kind to say expense or income")
    return -cents if neg else cents


def fmt(cents: int, currency: str = "BRL") -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    groups = f"{whole:,}"
    if currency == "BRL":
        groups = groups.replace(",", ".")
        return f"{sign}R$ {groups},{frac:02d}"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    return f"{sign}{symbol}{groups}.{frac:02d}"


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    init(con)
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tx (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc       TEXT    NOT NULL,
            day_local    TEXT    NOT NULL,
            month_local  TEXT    NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            kind         TEXT    NOT NULL CHECK (kind IN ('expense','income')),
            category     TEXT    NOT NULL,
            note         TEXT    NOT NULL DEFAULT '',
            source       TEXT    NOT NULL DEFAULT 'chat',
            created_utc  TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS tx_month ON tx (month_local);
        CREATE INDEX IF NOT EXISTS tx_day   ON tx (day_local);
        CREATE TABLE IF NOT EXISTS fixed (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            label        TEXT    NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            kind         TEXT    NOT NULL CHECK (kind IN ('expense','income')),
            day_of_month INTEGER NOT NULL DEFAULT 1,
            frequency    TEXT    NOT NULL DEFAULT 'monthly'
                         CHECK (frequency IN ('monthly','weekly','biweekly')),
            active       INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    # v1 -> v2: recurring lines gained a frequency. A ledger written by v1
    # holds monthly-only rows, which is exactly the DEFAULT, so the column can
    # be added in place with no data migration.
    cols = {r[1] for r in con.execute("PRAGMA table_info(fixed)")}
    if "frequency" not in cols:
        con.execute("ALTER TABLE fixed ADD COLUMN frequency TEXT NOT NULL"
                    " DEFAULT 'monthly'")

    con.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    con.execute("UPDATE config SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),))
    con.commit()


def get_cfg(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_cfg(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    con.commit()


def tz_of(con: sqlite3.Connection) -> ZoneInfo:
    """The owner's zone. Falls back to UTC loudly rather than to the
    container's clock, which is the fleet default and belongs to nobody."""
    name = get_cfg(con, "timezone") or os.environ.get("AGENT_TZ", "") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        print(f"warning: unknown timezone {name!r}, using UTC", file=sys.stderr)
        return ZoneInfo("UTC")


def currency_of(con: sqlite3.Connection) -> str:
    return get_cfg(con, "currency") or "BRL"


def now_local(con: sqlite3.Connection) -> datetime:
    return datetime.now(timezone.utc).astimezone(tz_of(con))


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------

def add_tx(con, amount_cents, kind, category, note="", source="chat", when=None):
    """Record one transaction, resolving its day in the OWNER's zone."""
    tz = tz_of(con)
    moment = (when.astimezone(tz) if when else datetime.now(timezone.utc).astimezone(tz))
    cur = con.execute(
        "INSERT INTO tx (ts_utc, day_local, month_local, amount_cents, kind,"
        " category, note, source, created_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
            moment.strftime("%Y-%m-%d"),
            moment.strftime("%Y-%m"),
            int(amount_cents),
            kind,
            (category or "other").strip().lower(),
            note.strip(),
            source,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    con.commit()
    return cur.lastrowid


def month_totals(con, month: str) -> dict:
    rows = con.execute(
        "SELECT kind, SUM(amount_cents) AS total, COUNT(*) AS n"
        " FROM tx WHERE month_local = ? GROUP BY kind",
        (month,),
    ).fetchall()
    out = {"month": month, "expense": 0, "income": 0, "count": 0}
    for r in rows:
        out[r["kind"]] = int(r["total"] or 0)
        out["count"] += int(r["n"])
    out["net"] = out["income"] - out["expense"]
    return out


def by_category(con, month: str, kind: str = "expense") -> list[dict]:
    rows = con.execute(
        "SELECT category, SUM(amount_cents) AS total, COUNT(*) AS n FROM tx"
        " WHERE month_local = ? AND kind = ? GROUP BY category"
        " ORDER BY total DESC",
        (month, kind),
    ).fetchall()
    return [{"category": r["category"], "total": int(r["total"]), "n": int(r["n"])} for r in rows]


# A month is not four weeks. Paying someone weekly and calling it 4x monthly
# loses 4.35 weeks a year -- about a month of income missing from every
# projection. 52/12 and 26/12 are the honest conversions.
PER_MONTH = {"monthly": 1.0, "weekly": 52 / 12, "biweekly": 26 / 12}


def monthly_equivalent(amount_cents: int, frequency: str) -> int:
    return int(round(amount_cents * PER_MONTH.get(frequency, 1.0)))


def fixed_totals(con) -> dict:
    """Recurring lines, normalised to what they cost or bring in per month."""
    rows = con.execute(
        "SELECT kind, amount_cents, frequency FROM fixed WHERE active = 1"
    ).fetchall()
    out = {"expense": 0, "income": 0}
    for r in rows:
        out[r["kind"]] += monthly_equivalent(int(r["amount_cents"]), r["frequency"])
    return out


def days_in_month(day: datetime) -> int:
    nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (nxt - timedelta(days=1)).day


def basis(con, today=None) -> dict:
    """How much history the projection is standing on.

    A projection from four days and no salary is arithmetic on almost
    nothing, and it always says the same thing: you cannot afford it. Left to
    a `_fmt` string the model has no way to tell that apart from a real
    verdict -- so the thinness is a FIELD, not something the skill has to
    notice. `usable` is what a caller keys off; the reasons say what to ask
    for.
    """
    today = today or now_local(con)
    days = con.execute(
        "SELECT COUNT(DISTINCT day_local) AS d FROM tx WHERE kind = 'expense'"
    ).fetchone()["d"] or 0
    fx = fixed_totals(con)
    month_income = month_totals(con, today.strftime("%Y-%m"))["income"]
    has_income = (fx["income"] + month_income) > 0

    reasons = []
    if days < 5:
        reasons.append("fewer than 5 days of recorded spending")
    if not has_income:
        reasons.append("no income recorded -- add a salary with `fixed add`")
    if not fx["expense"]:
        reasons.append("no fixed costs recorded, so the projection omits rent and bills")

    return {
        "days_of_history": days,
        "has_income": has_income,
        "has_fixed_costs": bool(fx["expense"]),
        "usable": not reasons,
        "reasons": reasons,
    }


def project_month(con, today=None) -> dict:
    """Project this month's close from the pace so far.

    Variable spend is extrapolated by daily run rate over elapsed days;
    fixed lines are added whole, whether or not they have landed yet. The
    projection is arithmetic, not a forecast model -- it says "at this pace",
    and the agent must say it that way.
    """
    today = today or now_local(con)
    month = today.strftime("%Y-%m")
    totals = month_totals(con, month)
    fx = fixed_totals(con)
    elapsed = today.day
    total_days = days_in_month(today)

    variable = totals["expense"]
    daily = variable / elapsed if elapsed else 0
    projected_variable = int(round(daily * total_days))
    projected_expense = projected_variable + fx["expense"]
    projected_income = totals["income"] + fx["income"]

    return {
        "month": month,
        "today": today.strftime("%Y-%m-%d"),
        "elapsed_days": elapsed,
        "total_days": total_days,
        "spent_so_far": variable,
        "daily_rate": int(round(daily)),
        "projected_variable": projected_variable,
        "fixed_expense": fx["expense"],
        "fixed_income": fx["income"],
        "projected_expense": projected_expense,
        "projected_income": projected_income,
        "projected_net": projected_income - projected_expense,
        "basis": basis(con, today=today),
    }


def simulate(con, amount_cents: int, installments: int = 1, today=None) -> dict:
    """What one purchase does to the month, and to the months it spills into.

    Answers "can I afford this?" with the same arithmetic as `project`, plus
    the purchase -- so the answer is checkable against the projection the
    agent just gave, which is the point.
    """
    if installments < 1:
        raise ValueError("installments must be >= 1")
    base = project_month(con, today=today)
    per = amount_cents // installments
    remainder = amount_cents - per * installments  # first instalment carries it

    first = per + remainder
    after_expense = base["projected_expense"] + first
    after_net = base["projected_income"] - after_expense

    return {
        **{f"base_{k}": v for k, v in base.items()},
        "purchase": amount_cents,
        "installments": installments,
        "per_installment": per,
        "first_installment": first,
        "projected_expense_after": after_expense,
        "projected_net_before": base["projected_net"],
        "projected_net_after": after_net,
        "fits_this_month": after_net >= 0,
        "swing": base["projected_net"] - after_net,
        "basis": base["basis"],
    }


def status(con, today=None) -> dict:
    """Everything the agent needs to orient itself, in one call.

    The model reached for `money status` unprompted on its first live
    conversation -- which is the strongest evidence there is that the command
    should exist. It answers "where does this person stand, and what is still
    missing", so a skill never has to assemble that from three calls and
    guesswork.
    """
    today = today or now_local(con)
    month = today.strftime("%Y-%m")
    totals = month_totals(con, month)
    b = basis(con, today=today)
    rows = con.execute(
        "SELECT label, amount_cents, kind, frequency, day_of_month FROM fixed"
        " WHERE active = 1 ORDER BY kind DESC, day_of_month").fetchall()
    demo = con.execute(
        "SELECT COUNT(*) AS n FROM tx WHERE source = 'demo'").fetchone()["n"]
    total_tx = con.execute("SELECT COUNT(*) AS n FROM tx").fetchone()["n"]

    return {
        "configured": {
            "timezone": get_cfg(con, "timezone") or None,
            "currency": get_cfg(con, "currency") or None,
        },
        "month": month,
        "expense": totals["expense"],
        "income": totals["income"],
        "net": totals["net"],
        "transactions": total_tx,
        "demo_transactions": demo,
        "all_data_is_demo": bool(demo) and demo == total_tx,
        "fixed": [dict(r) for r in rows],
        "basis": b,
        "ready": b["usable"] and bool(get_cfg(con, "timezone")),
        "next_step": _next_step(con, b),
    }


def _next_step(con, b) -> str | None:
    """The single most useful thing to ask for next, or None when set up."""
    if not get_cfg(con, "timezone"):
        return "ask which city they are in, to set the timezone"
    if not b["has_income"]:
        return "ask what they earn and how often (monthly, weekly, biweekly)"
    if not b["has_fixed_costs"]:
        return "ask for their fixed monthly costs, starting with rent"
    if b["days_of_history"] < 5:
        return "nothing to ask -- they just need to log a few days of spending"
    return None


def recent(con, limit: int = 20) -> list[dict]:
    rows = con.execute(
        "SELECT id, day_local, amount_cents, kind, category, note FROM tx"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# CLI -- every subcommand prints JSON, so the skill reads fields, not prose
# --------------------------------------------------------------------------

def emit(obj, currency="BRL") -> None:
    def money_fields(d):
        if not isinstance(d, dict):
            return d
        out = dict(d)
        for k, v in list(d.items()):
            if isinstance(v, int) and (
                k.endswith("_cents") or k in {"expense", "income", "net", "total", "purchase"}
                or k.startswith(("projected_", "base_projected_", "spent_", "fixed_", "daily_"))
                or k.endswith(("_installment", "_after", "_before")) or k == "swing"
            ):
                out[k + "_fmt"] = fmt(v, currency)
        return out

    if isinstance(obj, list):
        obj = [money_fields(x) for x in obj]
    else:
        obj = money_fields(obj)
    print(json.dumps(obj, ensure_ascii=False, indent=2))


class _Parser(argparse.ArgumentParser):
    """argparse exits with a usage string on stderr and code 2. That string is
    what reached someone's phone as an answer. Raising instead lets _run turn
    it into a readable JSON error carrying the valid choices."""

    def error(self, message):
        raise ValueError(f"{message}. try: {self.prog} --help")

def main(argv=None) -> int:
    p = _Parser(prog="money", description="the cfo ledger engine")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    a = sub.add_parser("add", help="record a transaction")
    a.add_argument("amount")
    a.add_argument("--kind", choices=["expense", "income"], default="expense")
    a.add_argument("--category", default="other")
    a.add_argument("--note", default="")
    a.add_argument("--source", default="chat")

    s = sub.add_parser("summary", help="one month's totals and categories")
    s.add_argument("--month", default=None, help="YYYY-MM (default: current)")

    sub.add_parser("project", help="project this month's close at the current pace")
    sub.add_parser("status", help="where this person stands and what is missing")

    sim = sub.add_parser("simulate", help="what a purchase does to the month")
    sim.add_argument("amount")
    sim.add_argument("--installments", type=int, default=1)

    r = sub.add_parser("recent", help="last transactions")
    r.add_argument("--limit", type=int, default=20)

    d = sub.add_parser("delete", help="remove a transaction by id")
    d.add_argument("id", type=int)

    f = sub.add_parser("fixed", help="manage recurring lines")
    fsub = f.add_subparsers(dest="fcmd", required=True)
    fa = fsub.add_parser("add")
    fa.add_argument("label")
    fa.add_argument("amount")
    fa.add_argument("--kind", choices=["expense", "income"], default="expense")
    fa.add_argument("--day", type=int, default=1,
                    help="day of month for monthly; for weekly, 1=Monday")
    fa.add_argument("--every", choices=["monthly", "weekly", "biweekly"],
                    default="monthly")
    fsub.add_parser("list")
    fr = fsub.add_parser("remove")
    fr.add_argument("id", type=int)

    c = sub.add_parser("config", help="read or write a setting")
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")

    args = p.parse_args(argv)
    con = connect()
    cur = currency_of(con)

    if args.cmd == "add":
        cents = parse_amount(args.amount)
        tid = add_tx(con, cents, args.kind, args.category, args.note, args.source)
        month = now_local(con).strftime("%Y-%m")
        emit({"id": tid, "amount_cents": cents, "kind": args.kind,
              "category": args.category, "note": args.note,
              **{k: v for k, v in month_totals(con, month).items() if k != "month"},
              "month": month}, cur)

    elif args.cmd == "summary":
        month = args.month or now_local(con).strftime("%Y-%m")
        emit({**month_totals(con, month), "categories": by_category(con, month),
              "currency": cur}, cur)

    elif args.cmd == "status":
        emit({**status(con), "currency": cur}, cur)

    elif args.cmd == "project":
        emit({**project_month(con), "currency": cur}, cur)

    elif args.cmd == "simulate":
        emit({**simulate(con, parse_amount(args.amount), args.installments),
              "currency": cur}, cur)

    elif args.cmd == "recent":
        emit(recent(con, args.limit), cur)

    elif args.cmd == "delete":
        con.execute("DELETE FROM tx WHERE id = ?", (args.id,))
        con.commit()
        emit({"deleted": args.id}, cur)

    elif args.cmd == "fixed":
        if args.fcmd == "add":
            cents = parse_amount(args.amount)
            c2 = con.execute(
                "INSERT INTO fixed (label, amount_cents, kind, day_of_month,"
                " frequency) VALUES (?,?,?,?,?)",
                (args.label, cents, args.kind, args.day, args.every))
            con.commit()
            emit({"id": c2.lastrowid, "label": args.label,
                  "amount_cents": cents, "frequency": args.every,
                  "monthly_equivalent_cents":
                      monthly_equivalent(cents, args.every)}, cur)
        elif args.fcmd == "list":
            rows = con.execute(
                "SELECT id, label, amount_cents, kind, day_of_month, frequency"
                " FROM fixed WHERE active = 1 ORDER BY day_of_month").fetchall()
            emit([dict(r) for r in rows], cur)
        else:
            con.execute("UPDATE fixed SET active = 0 WHERE id = ?", (args.id,))
            con.commit()
            emit({"removed": args.id}, cur)

    elif args.cmd == "config":
        if args.key and args.value is not None:
            set_cfg(con, args.key, args.value)
            emit({args.key: args.value})
        elif args.key:
            emit({args.key: get_cfg(con, args.key)})
        else:
            rows = con.execute("SELECT key, value FROM config ORDER BY key").fetchall()
            emit({r["key"]: r["value"] for r in rows})

    return 0


def _run(fn, argv):
    """Never let a traceback reach a phone.

    Twice now a raw Python traceback has been delivered to the owner as the
    answer to a question -- once argparse's usage string, once a
    ModuleNotFoundError from another skill. A SOUL.md rule did not stop it,
    and it never will: by the time the model sees the text, the damage is a
    copy-paste away, and the instruction competes with "report errors
    faithfully", which it should also do.

    So the guarantee moves into the tool. Every failure leaves here as JSON
    with an `error` the skill can read out in a sentence, and the traceback
    goes to stderr, where the logs keep it and the chat never sees it.
    """
    import traceback
    try:
        return fn(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        print(json.dumps({"error": str(exc.code), "ok": False},
                         ensure_ascii=False))
        return 1
    except Exception as exc:                     # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "ok": False,
            "say": "tell the owner in one sentence what did not work; "
                   "do not paste this at them",
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(_run(main, None))
