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

State lives in $CFO_DATA -- by default `cfo/` inside $HERMES_HOME, the
instance's own home in the container -- and never in this repo, which is code
only and carries nobody's data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 4

DEFAULT_CATEGORIES = [
    "food", "groceries", "transport", "housing", "utilities", "health",
    "education", "shopping", "leisure", "subscriptions", "fees", "other",
]

# How the money left. `debit` is anything that leaves the account the moment
# it is spent -- pix, cash, a debit card, a boleto. `credit` is a card that
# settles later: the purchase is spending TODAY, in this month's categories,
# but it leaves the account only when the invoice is paid. That is why "how
# much did I spend?" and "how much do I have?" are different questions with
# different answers. On 2026-09-08 the owner asked both in one evening and
# got the same number for each, and it matched neither the bank nor the
# card: card purchases were being subtracted from the balance, and the card
# payment was being counted as spending on top of them.
METHODS = ("debit", "credit")
METHOD_ALIASES = {
    "debit": "debit", "debito": "debit", "pix": "debit", "cash": "debit",
    "dinheiro": "debit", "boleto": "debit", "ted": "debit", "doc": "debit",
    "transfer": "debit", "transferencia": "debit", "account": "debit",
    "conta": "debit",
    "credit": "credit", "credito": "credit", "card": "credit",
    "cartao": "credit", "cc": "credit", "fatura": "credit",
}

# The one row kind that is neither spending nor income: money moving from the
# account to the card issuer to settle purchases already recorded. It lowers
# the balance and touches no category. Counted as an expense it doubles every
# card purchase -- which is exactly what a R$ 2.841,17 "paid my card bill"
# did on 2026-09-08.
CARD_PAYMENT = "card_payment"


def data_dir() -> Path:
    """Where the ledger lives: $CFO_DATA, else `cfo/` inside the agent's home.

    The home is NOT a constant. It is /opt/data on the base this agent was
    written against and /var/lib/hermes on the one a new install now gets, and
    the container tells us which through HERMES_HOME. Hard-coding either one
    puts the ledger somewhere nothing else looks: the panel, the brief gate and
    the usage report all resolve the same way, so a literal here would split
    them silently -- an agent answering from an empty ledger beside a full one.
    """
    home = os.environ.get("HERMES_HOME", "/opt/data")
    return Path(os.environ.get("CFO_DATA", f"{home}/cfo"))


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


CURRENCY_SYMBOL = re.compile(r"[$€£¥R]\s*(?=[\d.,])|[A-Za-z]{2,3}\$")
AMOUNT_IN_TEXT = re.compile(r"\d[\d.,]*\d|\d")


def refuse_symbol_in_amount(raw: str) -> None:
    """A currency symbol never reaches this argument, and here is why.

    On 2026-09-02 the owner texted "I just spent $54.82 on the market". The
    agent composed, correctly by its own instructions, `add "$54.82" --note "I
    just spent $54.82 on the market"` -- and the shell expanded `$5`, an unset
    positional parameter, to nothing. What ran was `add 4.82`, with a note
    that read "I just spent 4.82 on the market". R$ 4,82 went into the ledger
    for a R$ 54,82 purchase, and BOTH halves of the call agreed with each
    other, so nothing downstream could tell. It was caught because the agent
    reads the amount back in its confirmation and happened to notice.

    Nothing inside this process can detect that call: by the time argv exists
    the digits are gone. So the fix is to make the dangerous string never
    appear in a command line at all. The amount argument carries digits and
    separators, never a symbol -- and this refusal is what teaches that, on
    the one path where the symbol is still visible (single-quoted, where it
    was harmless) rather than in a rule the model reads once.

    The digits themselves are still passed EXACTLY as the owner wrote them:
    re-typing `1.234,56` into another format is how a thousands separator
    becomes a decimal point and R$ 1.234,56 becomes R$ 1,23.
    """
    if CURRENCY_SYMBOL.search(raw or ""):
        raise SystemExit(
            f"the amount argument takes digits only -- {raw!r} carries a "
            "currency symbol. Strip it and keep the digits exactly as they "
            "were written (54.82, 1.234,56), and quote arguments with '' "
            "rather than \"\": inside double quotes the shell eats `$5` and "
            "$54.82 becomes 4.82, silently and in agreement with itself. "
            "This is a mistake in the call and not news for the owner: fix "
            "the command and run it again, say nothing about it.")


def note_disagrees(raw_amount: str, note: str) -> str | None:
    """The note's own number, when the amount looks like a truncation of it.

    The mixed case the refusal above cannot reach: the note single-quoted and
    intact, the amount double-quoted and eaten. `4.82` is then a proper suffix
    of the note's `54.82`, which no ordinary log produces -- an amount is not
    normally the tail of a longer number sitting in the same sentence.
    """
    raw = (raw_amount or "").strip()
    if not raw or not note:
        return None
    for token in AMOUNT_IN_TEXT.findall(note):
        if token != raw and len(token) > len(raw) and token.endswith(raw):
            return token
    return None


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
            kind         TEXT    NOT NULL
                         CHECK (kind IN ('expense','income','transfer')),
            category     TEXT    NOT NULL,
            note         TEXT    NOT NULL DEFAULT '',
            source       TEXT    NOT NULL DEFAULT 'chat',
            created_utc  TEXT    NOT NULL,
            method       TEXT    NOT NULL DEFAULT 'debit'
                         CHECK (method IN ('debit','credit'))
        );
        CREATE INDEX IF NOT EXISTS tx_month ON tx (month_local);
        CREATE INDEX IF NOT EXISTS tx_day   ON tx (day_local);
        CREATE TABLE IF NOT EXISTS budget (
            category     TEXT PRIMARY KEY,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            updated_utc  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS merchant_category (
            merchant    TEXT PRIMARY KEY,
            label       TEXT NOT NULL,
            category    TEXT NOT NULL,
            updated_utc TEXT NOT NULL
        );
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

    # v2 -> v3: learned merchant names. A new table with no back-reference, so
    # CREATE TABLE IF NOT EXISTS above is the whole migration -- a v2 ledger
    # opens as v3 with an empty map and classifies exactly as it did before.

    # v3 -> v4: a payment method on every row and a third kind, `transfer`.
    # Both live in the CHECK constraints of `tx`, which SQLite cannot alter
    # in place, so this one is a rebuild -- see _migrate_tx_v4.
    _migrate_tx_v4(con)

    con.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    con.execute("UPDATE config SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),))
    con.commit()


def _migrate_tx_v4(con: sqlite3.Connection) -> None:
    """Rebuild `tx` with a `method` column and `transfer` as a legal kind.

    Every row that exists was money leaving the account when it was logged,
    so the old rows become `debit` -- the only reading that changes no total
    anybody has already been told. A v3 ledger opens as v4 and every command
    answers exactly as it did, until the first `--via credit` or `card pay`.

    A real person's ledger is being rewritten in place, so a copy is taken
    first through SQLite's own backup call -- a file copy of a WAL-mode
    database can miss the pages still in the log -- and the rebuild is one
    transaction: either the new table is there with every row, or the old
    one is untouched.
    """
    cols = {r[1] for r in con.execute("PRAGMA table_info(tx)")}
    if "method" in cols:
        return
    try:
        keep = sqlite3.connect(str(db_path()) + ".v3.bak")
        con.backup(keep)
        keep.close()
    except sqlite3.Error as exc:
        print(f"warning: could not back up the ledger before migrating: {exc}",
              file=sys.stderr)
    con.executescript(
        """
        BEGIN;
        CREATE TABLE tx_v4 (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc       TEXT    NOT NULL,
            day_local    TEXT    NOT NULL,
            month_local  TEXT    NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            kind         TEXT    NOT NULL
                         CHECK (kind IN ('expense','income','transfer')),
            category     TEXT    NOT NULL,
            note         TEXT    NOT NULL DEFAULT '',
            source       TEXT    NOT NULL DEFAULT 'chat',
            created_utc  TEXT    NOT NULL,
            method       TEXT    NOT NULL DEFAULT 'debit'
                         CHECK (method IN ('debit','credit'))
        );
        INSERT INTO tx_v4 (id, ts_utc, day_local, month_local, amount_cents,
                           kind, category, note, source, created_utc, method)
            SELECT id, ts_utc, day_local, month_local, amount_cents,
                   kind, category, note, source, created_utc, 'debit'
            FROM tx;
        DROP TABLE tx;
        ALTER TABLE tx_v4 RENAME TO tx;
        CREATE INDEX IF NOT EXISTS tx_month ON tx (month_local);
        CREATE INDEX IF NOT EXISTS tx_day   ON tx (day_local);
        COMMIT;
        """
    )


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


# The hours the brief gate opens on, held here rather than in a cron
# expression: the container's clock is the fleet's, the owner's is theirs.
# Defined here rather than in brief_gate.py so the default hour has ONE
# definition -- a gate that opens at 8 while `status` reports 9 is a bug
# nobody would think to look for.
BRIEF_SLOTS = (
    ("morning", "brief_hour", "8"),
    # On by default, and silent by default: cfo-brief sends an evening message
    # only when something was logged, something falls due tomorrow, or
    # something turned. A quiet evening is meant to produce no message at all.
    ("evening", "night_brief_hour", "22"),
)
BRIEF_HOUR_KEYS = tuple(key for _, key, _ in BRIEF_SLOTS)
HOUR_OFF = {"off", "none", "never", "no", "false", "-"}


def hour_or_none(raw: str, default: str) -> int | None:
    """A configured brief hour, or None when that slot is off.

    An unparseable value falls back to the DEFAULT, not to off: `validate_cfg`
    refuses anything but an hour or `off`, so a broken value here was
    hand-edited, and reading a typo as "the owner turned this off" is a
    silent unsubscribe nobody asked for.
    """
    val = (raw or "").strip().lower() or (default or "").strip().lower()
    if val in HOUR_OFF:
        return None
    try:
        hour = int(val)
    except ValueError:
        hour = -1
    if 0 <= hour <= 23:
        return hour
    print(f"warning: unusable brief hour {raw!r}, falling back to {default!r}",
          file=sys.stderr)
    return None if val == (default or "").strip().lower() else hour_or_none(default, default)


def brief_hours(con: sqlite3.Connection) -> dict:
    """{slot: hour in the owner's zone, or None when off}."""
    return {name: hour_or_none(get_cfg(con, key), default)
            for name, key, default in BRIEF_SLOTS}


def validate_cfg(key: str, value: str) -> str:
    """The value as it should be stored, or a refusal.

    `config` writes any key the agent hands it, which is right for a setting
    the model infers from a city name. It is wrong for the two that decide
    whether the brief fires at all: `config brief_hour 8h` would store `8h`,
    the gate would read it as unparseable, and the owner would find out by
    noticing, some week later, that the agent had stopped talking. There is
    no error to see -- silence is what a working schedule and a broken one
    both look like.
    """
    if key == "language":
        # "pt-BR", "Portuguese", "en_US" -> the two letters the panel and the
        # brief gate key off. Anything else is stored as typed and falls back
        # to the currency's language, which is what happened before this key
        # existed.
        val = (value or "").strip().lower()
        names = {"portuguese": "pt", "portugues": "pt", "português": "pt",
                 "english": "en", "ingles": "en", "inglês": "en",
                 "spanish": "es", "espanhol": "es", "español": "es"}
        return names.get(val, re.split(r"[-_]", val)[0][:2] or val)
    if key not in BRIEF_HOUR_KEYS:
        return value
    val = (value or "").strip().lower()
    if val in HOUR_OFF:
        return "off"
    try:
        hour = int(val)
    except ValueError:
        raise SystemExit(
            f"{key} must be an hour from 0 to 23, or `off` -- got {value!r}")
    if not 0 <= hour <= 23:
        raise SystemExit(
            f"{key} must be an hour from 0 to 23, or `off` -- got {value!r}")
    return str(hour)


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


# The language the owner writes in. Stored by the agent on first contact
# (`config language`), and until then inferred from the currency -- the one
# setting that is certainly answered before there is anything to say. The
# chat needs none of this: it mirrors the message in front of it. The brief
# and the panel have no message to mirror, which is how a morning brief went
# out in Portuguese to an owner who had written nothing but English for a
# week: every worked example in the skills is Portuguese, and with nothing
# else to go on, that was the loudest thing in the room.
CURRENCY_LANGUAGE = {"BRL": "pt", "EUR": "en", "USD": "en", "GBP": "en"}
LANGUAGE_NAMES = {"pt": "Portuguese", "en": "English", "es": "Spanish",
                  "fr": "French", "de": "German", "it": "Italian"}


def language_of(con: sqlite3.Connection) -> str:
    stored = (get_cfg(con, "language") or "").strip().lower()[:2]
    return stored or CURRENCY_LANGUAGE.get(currency_of(con), "en")


def now_local(con: sqlite3.Connection) -> datetime:
    return datetime.now(timezone.utc).astimezone(tz_of(con))


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------

WEEKDAYS = {
    "monday": 0, "mon": 0, "segunda": 0, "segunda-feira": 0, "seg": 0,
    "tuesday": 1, "tue": 1, "terca": 1, "terca-feira": 1, "ter": 1,
    "wednesday": 2, "wed": 2, "quarta": 2, "quarta-feira": 2, "qua": 2,
    "thursday": 3, "thu": 3, "quinta": 3, "quinta-feira": 3, "qui": 3,
    "friday": 4, "fri": 4, "sexta": 4, "sexta-feira": 4, "sex": 4,
    "saturday": 5, "sat": 5, "sabado": 5, "sab": 5,
    "sunday": 6, "sun": 6, "domingo": 6, "dom": 6,
}
RELATIVE_DAYS = {"today": 0, "hoje": 0, "yesterday": 1, "ontem": 1,
                 "anteontem": 2, "day before yesterday": 2}


def resolve_on(con, raw, today=None):
    """The moment a transaction the owner DATED should be stamped with.

    `raw` is what the owner said: an ISO date, `today`/`hoje`,
    `yesterday`/`ontem`, `anteontem`, or a weekday in English or Portuguese
    (`saturday`, `sábado`, `sab`) -- meaning the most recent one, today
    included. None means now.

    A past day is stamped at NOON in the owner's zone. Midnight is the one
    hour that changes date under a zone shift and noon the one that never
    does; the statement importer made the same choice for the same reason.
    Today keeps the real clock, so a row logged at 22:40 sorts after a
    balance the owner read off the bank at 22:34.

    The future is refused: a purchase dated tomorrow is a typo or a plan, and
    the ledger records neither.

    This exists because of 2026-09-08. The owner sat down at 22:00 and
    listed four days -- "Saturday I paid...", "Sunday...", "Monday...",
    "today..." -- and the engine could not take a date, so all four landed on
    the 8th. The next morning's brief opened with a "yesterday" four days'
    worth of spending big, and projected the month at a five-figure negative
    close. Every figure was correct arithmetic on rows that said what the
    owner had said; only the days were wrong, and nothing downstream could
    tell.
    """
    if raw is None or not str(raw).strip():
        return None
    now = today or now_local(con)
    key = strip_accents(str(raw)).strip().lower()
    if key in RELATIVE_DAYS:
        day = (now - timedelta(days=RELATIVE_DAYS[key])).date()
    elif key in WEEKDAYS:
        day = (now - timedelta(days=(now.weekday() - WEEKDAYS[key]) % 7)).date()
    else:
        try:
            day = datetime.strptime(key, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit(
                f"cannot read the day {raw!r} -- use YYYY-MM-DD, today, "
                "yesterday, or a weekday name (saturday, sábado)")
    if day > now.date():
        raise SystemExit(
            f"{day.isoformat()} is in the future -- the ledger records what "
            "happened, not what is planned")
    if day == now.date():
        return now
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz_of(con))


def normalise_method(raw: str | None) -> str:
    """`pix`, `cartão`, `cc`, `débito`... -> `debit` or `credit`."""
    if raw is None or not str(raw).strip():
        return "debit"
    key = strip_accents(str(raw)).strip().lower()
    try:
        return METHOD_ALIASES[key]
    except KeyError:
        raise SystemExit(
            f"unknown payment method {raw!r} -- use debit (pix, cash, boleto, "
            "debit card) or credit (a card that settles on an invoice)")


def add_tx(con, amount_cents, kind, category, note="", source="chat", when=None,
           method="debit"):
    """Record one transaction, resolving its day in the OWNER's zone."""
    tz = tz_of(con)
    moment = (when.astimezone(tz) if when else datetime.now(timezone.utc).astimezone(tz))
    cur = con.execute(
        "INSERT INTO tx (ts_utc, day_local, month_local, amount_cents, kind,"
        " category, note, source, created_utc, method)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            method if kind != "income" else "debit",
        ),
    )
    con.commit()
    return cur.lastrowid


# The channels a purchase is forwarded on without anyone typing: a Wallet tap
# from the phone, the bank's SMS from the phone, the bank's push notification
# mirrored to the Mac. One purchase can arrive on two or three of them within
# a minute, and nobody is there to notice the double.
FORWARDED = ("wallet", "sms", "push")
FORWARDED_WINDOW_MIN = 20


def forwarded_twin(con, cents: int, source: str, minutes: int = FORWARDED_WINDOW_MIN):
    """A row for the same amount, from ANOTHER forwarding channel, minutes ago.

    Only across channels: two R$ 5,00 coffees tapped ten minutes apart are two
    coffees, and both stay. A tap and an SMS for one R$ 5,00 are one coffee.
    """
    if source not in FORWARDED:
        return None
    others = [s for s in FORWARDED if s != source]
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return con.execute(
        "SELECT id, day_local, note, source, method, category FROM tx"
        " WHERE amount_cents = ? AND kind = 'expense' AND source IN (?, ?)"
        " AND created_utc >= ? ORDER BY id DESC LIMIT 1",
        (cents, *others, since)).fetchone()


def record_forwarded(con, cents: int, kind: str, category: str, note: str,
                     source: str, method: str | None = None,
                     card: str | None = None, when=None) -> dict:
    """Log a purchase the phone or the Mac forwarded, once, with the right method.

    The bank's own text is the authority on DÉBITO vs CRÉDITO -- in Brazil one
    card is both, and a Wallet tap does not say which function was used. So
    when the bank's alert arrives after the tap for the same amount, the tap's
    row is not duplicated: it is CORRECTED to what the bank said. `method`
    here is the explicit reading (None when the text said nothing); a tap
    with nothing explicit falls back to what the named card was set to, else
    credit.
    """
    resolved = method or (method_of_card(con, card) if card else "credit")
    if kind == "expense":
        twin = forwarded_twin(con, cents, source)
        if twin:
            updated = False
            if method and twin["method"] != method:
                con.execute("UPDATE tx SET method = ? WHERE id = ?", (method, twin["id"]))
                con.commit()
                updated = True
            return {"skipped": True, "duplicate_of": twin["id"],
                    "already_logged_from": twin["source"],
                    "amount_cents": cents, "note": twin["note"],
                    "category": twin["category"],
                    "method": method if updated else twin["method"],
                    "method_updated": updated,
                    "say": ("already recorded from the other channel"
                            + (" -- its method is now " + method if updated else "")
                            + "; reply with nothing, or one word")}
    tid = add_tx(con, cents, kind, category, note=note, source=source,
                 when=when, method=resolved)
    row = get_tx(con, tid)
    return {"id": tid, "amount_cents": cents, "kind": kind, "method": row["method"],
            "category": row["category"], "note": row["note"], "day": row["day_local"],
            "source": source, "skipped": False}


def get_tx(con, tid: int) -> dict:
    row = con.execute("SELECT * FROM tx WHERE id = ?", (tid,)).fetchone()
    if not row:
        raise SystemExit(f"no transaction with id {tid}")
    return dict(row)


def edit_tx(con, tid: int, when=None, amount_cents=None, kind=None,
            category=None, note=None, method=None) -> dict:
    """Change a row in place, and say what it was before.

    Corrections used to be `delete` then `add` -- two calls, a new id, and a
    row re-stamped with today's date even when the owner was fixing
    Saturday's amount. `edit` keeps the id and touches only what was named:
    moving a row to another day recomputes its day and month in the owner's
    zone, and nothing else about it changes.
    """
    before = get_tx(con, tid)
    after = dict(before)
    if when is not None:
        after["ts_utc"] = when.astimezone(timezone.utc).isoformat(timespec="seconds")
        after["day_local"] = when.strftime("%Y-%m-%d")
        after["month_local"] = when.strftime("%Y-%m")
    if amount_cents is not None:
        after["amount_cents"] = int(amount_cents)
    if kind is not None:
        after["kind"] = kind
    if category is not None:
        after["category"] = category.strip().lower()
    if note is not None:
        after["note"] = note.strip()
    if method is not None:
        after["method"] = method
    con.execute(
        "UPDATE tx SET ts_utc=?, day_local=?, month_local=?, amount_cents=?,"
        " kind=?, category=?, note=?, method=? WHERE id=?",
        (after["ts_utc"], after["day_local"], after["month_local"],
         after["amount_cents"], after["kind"], after["category"],
         after["note"], after["method"], tid))
    con.commit()
    changed = sorted(k for k in after if after[k] != before[k])
    return {"id": tid, "changed": changed, "before": before, "after": after}


def last_logged(con) -> dict | None:
    """The newest row the owner logged in the chat -- what "undo" means.

    Never a demo row and never an imported one: "apaga o último" after an
    import would silently drop one line of a statement, and the owner would
    be told a whole import was reversed when it was not. Imports have their
    own undo, by batch.
    """
    row = con.execute(
        "SELECT * FROM tx WHERE source NOT IN ('demo')"
        " AND source NOT LIKE 'import:%' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def month_totals(con, month: str) -> dict:
    rows = con.execute(
        "SELECT kind, SUM(amount_cents) AS total, COUNT(*) AS n"
        " FROM tx WHERE month_local = ? GROUP BY kind",
        (month,),
    ).fetchall()
    out = {"month": month, "expense": 0, "income": 0, "count": 0,
           "card_paid": 0}
    for r in rows:
        if r["kind"] == "transfer":
            # Settles purchases already in `expense`; not spending, not
            # income, and not in the count of things that happened.
            out["card_paid"] = int(r["total"] or 0)
            continue
        out[r["kind"]] = int(r["total"] or 0)
        out["count"] += int(r["n"])
    out["net"] = out["income"] - out["expense"]
    return out


def day_totals(con, day: str, today=None) -> dict:
    """One day, in the owner's own zone.

    The brief is meant to open with what yesterday cost, and it could not:
    nothing here returned a single day, and the one rule this agent has
    forbids the model adding the rows up itself. So it obeyed both rules,
    dropped its own lead sentence every morning, and sent a monthly
    projection instead -- which is a bank app, not a manager. The README's
    own example, "Bom dia. Ontem R$ 87,00.", was unreachable.

    `partial` says the day is still being spent. It exists for the evening
    brief: at 22:00 the day has two hours left, and a total announced as
    closed is one the morning brief will contradict tomorrow with a bigger
    number for the same date. Both figures are correct and the agent looks
    like it cannot count -- which costs more trust than the missing number
    ever bought. The field is here rather than in the skill because a rule in
    prose is one the model can reason its way around at 22:00.
    """
    rows = con.execute(
        "SELECT kind, SUM(amount_cents) AS total, COUNT(*) AS n"
        " FROM tx WHERE day_local = ? GROUP BY kind",
        (day,),
    ).fetchall()
    out = {"day": day, "expense": 0, "income": 0, "count": 0, "card_paid": 0}
    for r in rows:
        if r["kind"] == "transfer":
            out["card_paid"] = int(r["total"] or 0)
            continue
        out[r["kind"]] = int(r["total"] or 0)
        out["count"] += int(r["n"])
    out["net"] = out["income"] - out["expense"]
    out["partial"] = day == (today or now_local(con)).strftime("%Y-%m-%d")
    out["categories"] = [
        {"category": r["category"], "total": int(r["total"]), "n": int(r["n"])}
        for r in con.execute(
            "SELECT category, SUM(amount_cents) AS total, COUNT(*) AS n FROM tx"
            " WHERE day_local = ? AND kind = 'expense' GROUP BY category"
            " ORDER BY total DESC", (day,)).fetchall()
    ]
    return out


def yesterday_local(con, today=None) -> str:
    """The day before the owner's today -- resolved in their zone, not UTC.

    At 08:00 in Sao Paulo it is 11:00 UTC, so both agree; at 08:00 in Los
    Angeles it is 15:00 UTC and they still agree. They part on the first of
    the month at either edge, which is exactly when the brief is read and
    exactly when getting it wrong reports an empty day.
    """
    now = today or now_local(con)
    return (now - timedelta(days=1)).strftime("%Y-%m-%d")


def daily_totals(con, month: str, today=None) -> list[dict]:
    """What each day of `month` cost, zeros included, in the owner's zone.

    Zeros included on purpose: the caller is drawing a shape, and a series
    that skips the days nothing was spent draws a month with no quiet days in
    it. A month still running stops at the owner's today rather than padding
    the future with zeros that would read as days where nothing was spent.
    """
    spent = {r["day_local"]: int(r["total"]) for r in con.execute(
        "SELECT day_local, SUM(amount_cents) AS total FROM tx"
        " WHERE month_local = ? AND kind = 'expense' GROUP BY day_local",
        (month,)).fetchall()}
    today = today or now_local(con)
    first = datetime.strptime(month + "-01", "%Y-%m-%d")
    last = (today.day if month == today.strftime("%Y-%m")
            else days_in_month(first))
    return [{"day": f"{month}-{d:02d}", "expense": spent.get(f"{month}-{d:02d}", 0)}
            for d in range(1, last + 1)]


def _due_on(year: int, month: int, day_of_month: int) -> datetime:
    """A fixed line's date in one month, clamped to that month's length.

    Rent on the 31st is still rent in April. Clamping to the 30th keeps it in
    the month it belongs to; skipping it would drop a bill from the one list
    whose whole job is that no bill is a surprise.
    """
    first = datetime(year, month, 1)
    return first.replace(day=min(day_of_month, days_in_month(first)))


def upcoming_fixed(con, today=None, within_days: int = 7) -> list[dict]:
    """The recurring lines that fall due within the next `within_days` days.

    This is date arithmetic, which is exactly what the owner's agent may not
    do: SOUL.md forbids the model any calculation, and "the condominium is
    due tomorrow" is a subtraction over a month boundary -- on the 29th, a
    bill on the 6th is eight days away, not minus twenty-three. The brief
    asked the model to notice that from `status.fixed[].day_of_month` and a
    date, which is a rule in prose where a field belongs.

    Monthly lines only. `weekly` and `biweekly` store a weekday in
    `day_of_month` (`fixed add --day 1` means Monday), so reading it as a
    date would answer confidently and wrongly -- the failure mode this whole
    engine exists to prevent.
    """
    today = today or now_local(con)
    rows = con.execute(
        "SELECT id, label, amount_cents, kind, day_of_month FROM fixed"
        " WHERE active = 1 AND frequency = 'monthly'").fetchall()

    out = []
    for r in rows:
        day = int(r["day_of_month"])
        due = _due_on(today.year, today.month, day)
        if due.date() < today.date():
            nxt = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            due = _due_on(nxt[0], nxt[1], day)
        away = (due.date() - today.date()).days
        if away <= within_days:
            out.append({
                "id": r["id"], "label": r["label"],
                "amount_cents": int(r["amount_cents"]), "kind": r["kind"],
                "due": due.strftime("%Y-%m-%d"), "due_day": due.day,
                "days_away": away,
            })
    return sorted(out, key=lambda d: (d["days_away"], -d["amount_cents"]))


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


def expected_income(con) -> int:
    """What this person typically earns in a month, when no fixed salary
    exists. Freelance, contract and commission income is the normal case for
    a lot of people, and a projection that demands a fixed salary tells all
    of them they are broke."""
    raw = get_cfg(con, "expected_income")
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


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
    has_income = (fx["income"] + month_income + expected_income(con)) > 0

    elapsed = today.day

    reasons = []
    if days < 5:
        reasons.append("fewer than 5 days of recorded spending")
    if elapsed < 5:
        # The guard above counts the whole ledger; this one counts the month
        # being projected, and they come apart at exactly the wrong moment.
        # On the 1st, a ledger with 49 days of history says `usable` while the
        # pace is a single day multiplied by thirty: one trip to the shop at
        # 14:44 became R$ 2.109,60 of projected variable spending, and the
        # 08:00 brief before it -- nothing logged yet -- announced the month
        # would close at exactly the fixed costs. Both figures are real,
        # sourced and formatted, which is what makes them worth guarding.
        reasons.append(
            f"only {elapsed} day(s) of {today.strftime('%Y-%m')} have elapsed"
            " -- a month extrapolated from that is not a pace")
    if not has_income:
        reasons.append("no income recorded -- add a salary with `fixed add`")
    if not fx["expense"]:
        reasons.append("no fixed costs recorded, so the projection omits rent and bills")

    return {
        "days_of_history": days,
        "elapsed_days": elapsed,
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
    # Income booked this month, plus any fixed line, or -- for someone
    # without a fixed salary -- their declared typical month, whichever is
    # larger. Early in a month a freelancer has often booked nothing yet, and
    # projecting zero income makes every purchase unaffordable.
    projected_income = max(totals["income"] + fx["income"],
                           expected_income(con))

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

    # What is in the account, and what would be after the first instalment
    # -- when the owner has given a reading. "It fits the month" and "I
    # have it in the account today" are different questions; a purchase
    # can pass the first and fail the second on the 3rd of the month.
    bal = balance(con, today=today)
    account = ({"balance_now_cents": bal["balance_cents"],
                "balance_after_first_cents": bal["balance_cents"] - first,
                "card_open_cents": bal["card_open_cents"]}
               if bal["has_anchor"] else
               {"balance_now_cents": None, "balance_after_first_cents": None,
                "card_open_cents": bal["card_open_cents"]})

    return {
        **account,
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

    bal = balance(con, today=today)
    bud = budgets(con, today=today)
    return {
        "configured": {
            "timezone": get_cfg(con, "timezone") or None,
            "currency": get_cfg(con, "currency") or None,
            # The language the owner writes in, stored once so the brief --
            # which has no message to mirror -- and the panel speak it too.
            "language": get_cfg(con, "language") or None,
            # In the owner's own zone, which is what the gate opens on. Here
            # so "a que horas voce me manda o resumo?" is a field to read
            # rather than a cron expression nobody in the chat can see.
            **{key: brief_hours(con)[name] for name, key, _ in BRIEF_SLOTS},
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
        "balance": {
            "has_anchor": bal["has_anchor"],
            "balance_cents": bal["balance_cents"],
            "anchor_age_days": bal.get("anchor_age_days"),
            "card_open_cents": bal["card_open_cents"],
        },
        "budgets": {"count": bud["count"], "attention": bud["attention"]},
        "ready": b["usable"] and bool(get_cfg(con, "timezone")),
        "next_step": _next_step(con, b, bal),
    }


def _next_step(con, b, bal=None) -> str | None:
    """The single most useful thing to ask for next, or None when set up."""
    if not get_cfg(con, "timezone"):
        return "ask which city they are in, to set the timezone"
    if not b["has_income"]:
        return ("ask what they earn and how often -- and if it varies, set a "
                "typical month with `config expected_income`")
    if not b["has_fixed_costs"]:
        return "ask for their fixed monthly costs, starting with rent"
    if bal is not None and not bal["has_anchor"]:
        # The one question that makes "how much do I have?" answerable.
        # Asked after income and bills because those are what the answer is
        # then kept current with.
        return ("ask what is in their account right now, and record it with "
                "`balance set` -- without it 'how much do I have?' has no answer")
    if b["days_of_history"] < 5:
        return "nothing to ask -- they just need to log a few days of spending"
    return None


# --------------------------------------------------------------------------
# merchants: naming a shop is classification, and it has to outlive the month
# --------------------------------------------------------------------------

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


def merchant_tokens(s: str) -> list[str]:
    """A name reduced to comparable words: accents off, case off, punctuation
    gone. `Droga Raia*` and `DROGARIA  RAIA` both become plain word lists."""
    return re.findall(r"[0-9a-z]+", strip_accents(s).lower())


def merchant_matches(merchant: str, note: str) -> bool:
    """Whole words, in order -- never a bare substring.

    `LIKE '%raia%'` also matches "PRAIA GRANDE ESTACIONAMENTO". Map the
    pharmacy and a parking lot silently becomes health spending, filed wrong
    for good, while the reclassified count reads like success -- the failure
    every bug in this ledger has had in common. Short merchant names are the
    normal case in a real statement (Raia, Duda, Sesc, Ipiranga), so the match
    has to respect word edges.
    """
    want = merchant_tokens(merchant)
    if not want:
        return False
    have = merchant_tokens(note)
    n = len(want)
    return any(have[i:i + n] == want for i in range(len(have) - n + 1))


def learn_merchant(con, merchant: str, category: str) -> str:
    """Remember one merchant->category decision. Returns the stored key."""
    key = " ".join(merchant_tokens(merchant))
    con.execute(
        "INSERT INTO merchant_category (merchant, label, category, updated_utc)"
        " VALUES (?,?,?,?) ON CONFLICT(merchant) DO UPDATE SET"
        " label = excluded.label, category = excluded.category,"
        " updated_utc = excluded.updated_utc",
        (key, merchant.strip(), category,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return key


def learned_categories(con) -> list[tuple[str, str]]:
    """Every merchant already named, longest name first.

    Longest first so the specific beats the general: once both are known,
    "posto ipiranga" is decided before "posto".
    """
    rows = con.execute(
        "SELECT merchant, category FROM merchant_category").fetchall()
    return sorted(((r["merchant"], r["category"]) for r in rows),
                  key=lambda mc: -len(merchant_tokens(mc[0])))


def categorize_learned(note: str, learned: list[tuple[str, str]]) -> str | None:
    for merchant, category in learned:
        if merchant_matches(merchant, note):
            return category
    return None


def uncategorized(con, limit: int = 40) -> dict:
    """The merchants the rules could not place, worst first.

    Keyword rules get the chains and miss everything local -- which in a real
    statement is most of it. Naming a merchant is classification, not
    arithmetic, and a model is genuinely good at it, so this hands it the
    SHORT list: distinct payees, deduplicated, with what each one costs. A
    hundred and fifty rows becomes twenty names.
    """
    rows = con.execute(
        "SELECT note, COUNT(*) AS n, SUM(amount_cents) AS total FROM tx"
        " WHERE category = 'other' AND kind = 'expense'"
        " GROUP BY note ORDER BY total DESC").fetchall()

    merchants: dict[str, dict] = {}
    for r in rows:
        label = re.sub(r"\s*#[0-9a-f]{16}$", "", r["note"] or "").strip()
        key = label.lower()[:40]
        if key not in merchants:
            merchants[key] = {"merchant": label, "count": 0, "total": 0}
        merchants[key]["count"] += int(r["n"])
        merchants[key]["total"] += int(r["total"])

    ranked = sorted(merchants.values(), key=lambda x: -x["total"])[:limit]
    total_other = sum(m["total"] for m in merchants.values())
    return {
        "merchants": ranked,
        "distinct": len(merchants),
        "total_uncategorised": total_other,
        "categories": DEFAULT_CATEGORIES,
        "next": ("classify these by name and write them back with "
                 "`recategorize --map '{\"MERCHANT\": \"category\"}'`; "
                 "leave anything genuinely unclear as other. What you name "
                 "here is remembered and applied to future imports, so it is "
                 "asked once, not every month"),
    }


def recategorize(con, mapping: dict) -> dict:
    """Apply a merchant->category map to matching rows, and remember it.

    Remembering is the point. Naming forty merchants no keyword rule could
    hold is real work, and if it lands only on the rows that happen to be in
    the ledger today, next month's statement arrives as forty unknowns again
    and the owner is asked the same questions twice. The map is the asset;
    the UPDATE is just its first application.
    """
    changed = 0
    learned = 0
    unknown = []
    skipped = []

    rows = con.execute(
        "SELECT id, note FROM tx WHERE category = 'other' AND kind = 'expense'"
    ).fetchall()
    done: set[int] = set()

    for merchant, category in mapping.items():
        if category not in DEFAULT_CATEGORIES:
            unknown.append(category)
            continue
        if not merchant_tokens(merchant):
            skipped.append(merchant)  # punctuation only: matches everything
            continue

        hits = [r["id"] for r in rows
                if r["id"] not in done and merchant_matches(merchant, r["note"])]
        if hits:
            con.executemany("UPDATE tx SET category = ? WHERE id = ?",
                            [(category, i) for i in hits])
            done.update(hits)
            changed += len(hits)

        learn_merchant(con, merchant, category)
        learned += 1

    con.commit()
    return {"reclassified": changed, "learned": learned,
            "rejected_categories": unknown, "unusable_names": skipped,
            "still_other": uncategorized(con)["distinct"],
            "note": "these names are remembered and applied to future imports"}


def recent(con, limit: int = 20) -> list[dict]:
    rows = con.execute(
        "SELECT id, day_local, amount_cents, kind, method, category, note,"
        " source FROM tx ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# balance: "how much do I have?" -- the question a ledger cannot answer alone
# --------------------------------------------------------------------------

def balance_anchor(con) -> dict | None:
    raw = get_cfg(con, "balance_anchor")
    if not raw:
        return None
    try:
        a = json.loads(raw)
        return {"cents": int(a["cents"]), "utc": str(a["utc"]), "day": str(a["day"]),
                "after_id": int(a.get("after_id", 0))}
    except (ValueError, KeyError, TypeError):
        print("warning: unreadable balance anchor, ignoring it", file=sys.stderr)
        return None


def set_balance(con, cents: int, when=None) -> dict:
    """Anchor the account at a figure the owner read off their bank.

    Not a transaction, and not something the ledger could ever work out: it
    never saw the opening figure, and a card settles later than the purchase
    it paid for. The owner's own reading is the one fact that makes the
    question answerable. From that instant the balance is the anchor, plus
    income, minus what LEFT the account -- debit spending and card payments
    -- and a card purchase does not move it until the invoice is paid.

    The owner tried to say this on 2026-09-08 -- "adjust my balance to
    1.312,40" -- and was refused, correctly by the rules of the time, because
    the only way to hold a figure was a fake transaction. This is the other
    way. Set it again whenever they read a fresh number; the newest anchor
    wins and nothing before it is counted twice.
    """
    moment = when or now_local(con)
    # The id of the newest row at this instant. Timestamps are whole seconds
    # and the agent runs `balance set` and the next `add` in one breath, so
    # "after the anchor" has to be decided by id when the seconds tie.
    last = con.execute("SELECT COALESCE(MAX(id), 0) AS n FROM tx").fetchone()["n"]
    anchor = {"cents": int(cents),
              "utc": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
              "day": moment.strftime("%Y-%m-%d"),
              "after_id": int(last)}
    set_cfg(con, "balance_anchor", json.dumps(anchor))
    return anchor


def _sum_since(con, utc: str, where: str, after_id: int = 0) -> int:
    """Rows after an instant -- later by timestamp, or the same second and a
    higher id. A row backdated to noon last Saturday has an earlier timestamp
    than an anchor read on Monday, so it stays out, which is right: the bank
    already knew about Saturday when the owner read the figure."""
    row = con.execute(
        f"SELECT COALESCE(SUM(amount_cents), 0) AS t FROM tx"
        f" WHERE (ts_utc > ? OR (ts_utc = ? AND id > ?)) AND {where}",
        (utc, utc, after_id)).fetchone()
    return int(row["t"])


def card_open(con) -> dict:
    """Credit purchases not yet settled -- what the next invoice already
    holds. Counted from the last card payment; all of them if there has
    never been one."""
    last = con.execute(
        "SELECT id, ts_utc, day_local FROM tx WHERE kind = 'transfer'"
        " ORDER BY ts_utc DESC, id DESC LIMIT 1").fetchone()
    since, after = (last["ts_utc"], last["id"]) if last else ("", 0)
    return {"card_open_cents": _sum_since(con, since, "kind = 'expense' AND method = 'credit'",
                                          after_id=after),
            "card_paid_on": last["day_local"] if last else None}


def card_key(name: str) -> str:
    return "card:" + " ".join(merchant_tokens(name))


def set_card(con, name: str, method: str) -> dict:
    """Remember what a named card is. A Wallet tap arrives with the card's
    name and nothing else; without this, every tap is credit."""
    if not merchant_tokens(name):
        raise SystemExit(f"a card needs a name, got {name!r}")
    set_cfg(con, card_key(name), method)
    return {"card": name.strip(), "method": method}


def method_of_card(con, name: str | None) -> str:
    """The method a named card was set to, else credit -- what a card in a
    phone wallet almost always is."""
    if not name or not merchant_tokens(name):
        return "credit"
    return get_cfg(con, card_key(name)) or "credit"


def cards(con) -> list[dict]:
    rows = con.execute("SELECT key, value FROM config WHERE key LIKE 'card:%'"
                       " ORDER BY key").fetchall()
    return [{"card": r["key"][5:], "method": r["value"]} for r in rows]


def pay_card(con, cents: int, when=None, note: str = "") -> int:
    """Settle the card: money leaves the account, no category moves."""
    return add_tx(con, cents, "transfer", CARD_PAYMENT,
                  note=note or "card payment", when=when, method="debit")


# An anchor this old has drifted: something was paid that nobody logged. Not
# unusable -- the arithmetic is still right on what was logged -- but worth a
# fresh reading, which the brief can ask for.
ANCHOR_STALE_DAYS = 14


def balance(con, today=None) -> dict:
    """What is in the account now, from the last reading plus what moved.

    `basis.usable` is false with no anchor, and the reason says what to ask.
    A verdict built without one -- income minus expenses since the dawn of
    the ledger -- is the number the owner was given on 2026-09-08, and it
    was wrong by exactly the opening balance nobody had ever been asked for.
    """
    today = today or now_local(con)
    anchor = balance_anchor(con)
    on_card = card_open(con)
    due = [d for d in upcoming_fixed(con, today=today, within_days=7)
           if d["kind"] == "expense"]
    due_cents = sum(d["amount_cents"] for d in due)
    if not anchor:
        return {
            "has_anchor": False,
            "balance_cents": None,
            **on_card,
            "due_soon_cents": due_cents,
            "basis": {"usable": False, "stale": False, "reasons": [
                "no balance on file -- ask what is in the account right now "
                "and record it with `balance set`"]},
        }
    utc, after = anchor["utc"], anchor["after_id"]
    income_since = _sum_since(con, utc, "kind = 'income'", after)
    debit_since = _sum_since(con, utc, "kind = 'expense' AND method = 'debit'", after)
    paid_since = _sum_since(con, utc, "kind = 'transfer'", after)
    now_cents = anchor["cents"] + income_since - debit_since - paid_since
    age = (today.date() - datetime.strptime(anchor["day"], "%Y-%m-%d").date()).days
    reasons = []
    if age > ANCHOR_STALE_DAYS:
        reasons.append(f"the last balance reading is {age} days old -- worth "
                       "asking for a fresh one")
    return {
        "has_anchor": True,
        "anchor_cents": anchor["cents"],
        "anchor_day": anchor["day"],
        "anchor_age_days": age,
        "income_since_cents": income_since,
        "debit_since_cents": debit_since,
        "card_paid_since_cents": paid_since,
        "balance_cents": now_cents,
        **on_card,
        "due_soon_cents": due_cents,
        "due_soon": due,
        "after_due_soon_cents": now_cents - due_cents,
        "basis": {"usable": True, "stale": age > ANCHOR_STALE_DAYS,
                  "reasons": reasons},
    }


# --------------------------------------------------------------------------
# budgets: a ceiling per category, and who is near it
# --------------------------------------------------------------------------

BUDGET_ATTENTION_PCT = 80


def set_budget(con, category: str, cents: int) -> dict:
    category = category.strip().lower()
    if category not in DEFAULT_CATEGORIES:
        raise SystemExit(f"unknown category {category!r} -- one of "
                         + ", ".join(DEFAULT_CATEGORIES))
    con.execute(
        "INSERT INTO budget (category, amount_cents, updated_utc) VALUES (?,?,?)"
        " ON CONFLICT(category) DO UPDATE SET amount_cents = excluded.amount_cents,"
        " updated_utc = excluded.updated_utc",
        (category, int(cents), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return {"category": category, "limit_cents": int(cents)}


def remove_budget(con, category: str) -> dict:
    n = con.execute("DELETE FROM budget WHERE category = ?",
                    (category.strip().lower(),)).rowcount
    con.commit()
    return {"category": category.strip().lower(), "removed": n}


def budgets(con, today=None) -> dict:
    """Every budget against this month's spending, worst first.

    `attention` names the categories at 80% or over -- the list the brief
    reads. `on_pace_to_pass` is the same extrapolation `project` makes, per
    category, and it is None until the month has enough days to carry one:
    a ceiling "passed at this pace" on the 2nd is one dinner times thirty.
    """
    today = today or now_local(con)
    month = today.strftime("%Y-%m")
    spent = {r["category"]: r["total"] for r in by_category(con, month)}
    elapsed, total_days = today.day, days_in_month(today)
    pace_ok = elapsed >= 5
    out = []
    for r in con.execute("SELECT category, amount_cents FROM budget").fetchall():
        limit = int(r["amount_cents"])
        used = int(spent.get(r["category"], 0))
        pct = round(100 * used / limit) if limit else 0
        projected = int(round(used / elapsed * total_days)) if pace_ok else None
        out.append({
            "category": r["category"],
            "limit_cents": limit,
            "spent_cents": used,
            "left_cents": limit - used,
            "pct": pct,
            "over": used > limit,
            "projected_cents": projected,
            "on_pace_to_pass": (projected > limit) if projected is not None else None,
        })
    out.sort(key=lambda b: -b["pct"])
    return {
        "month": month,
        "budgets": out,
        "count": len(out),
        "attention": [b["category"] for b in out if b["pct"] >= BUDGET_ATTENTION_PCT],
    }


# --------------------------------------------------------------------------
# week: the seven days someone can still remember
# --------------------------------------------------------------------------

def _span_expense(con, first, last) -> int:
    row = con.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS t FROM tx WHERE kind = 'expense'"
        " AND day_local BETWEEN ? AND ?",
        (first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"))).fetchone()
    return int(row["t"])


def week(con, today=None) -> dict:
    """This week so far against the SAME days of last week.

    Monday-to-today against a whole previous week is a comparison that
    always says this week is cheaper, until Sunday, when it suddenly is not.
    The honest pair is Monday-to-Wednesday against last Monday-to-Wednesday;
    the full previous week is returned too, labelled as such.
    """
    today = today or now_local(con)
    monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    prev_monday = monday - timedelta(days=7)
    same_days = today.weekday()   # 0 on Monday: compare Monday with Monday
    this_week = _span_expense(con, monday, today)
    last_same = _span_expense(con, prev_monday, prev_monday + timedelta(days=same_days))
    last_full = _span_expense(con, prev_monday, monday - timedelta(days=1))
    top = con.execute(
        "SELECT category, SUM(amount_cents) AS total FROM tx WHERE kind = 'expense'"
        " AND day_local BETWEEN ? AND ? GROUP BY category ORDER BY total DESC LIMIT 1",
        (monday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))).fetchone()
    return {
        "week_start": monday.strftime("%Y-%m-%d"),
        "today": today.strftime("%Y-%m-%d"),
        "days_elapsed": same_days + 1,
        "this_week_cents": this_week,
        "last_week_same_days_cents": last_same,
        "delta_cents": this_week - last_same,
        "last_week_full_cents": last_full,
        "top_category": ({"category": top["category"], "total_cents": int(top["total"])}
                         if top else None),
    }


# --------------------------------------------------------------------------
# export: the owner's rows, as a file the owner can open anywhere
# --------------------------------------------------------------------------

def export_csv(con, month: str | None = None) -> dict:
    """One month -- or everything -- as CSV under $CFO_DATA/export.

    The ledger is the owner's. A file they can open in a spreadsheet is the
    proof, and the way out if they ever leave: nothing here is locked in.
    Written next to the ledger, never anywhere shared; sending it is the
    agent's act, on request, into the owner's own chat.
    """
    import csv
    where, params = ("WHERE month_local = ?", (month,)) if month else ("", ())
    rows = con.execute(
        f"SELECT id, day_local, kind, method, category, amount_cents, note, source"
        f" FROM tx {where} ORDER BY day_local, id", params).fetchall()
    target = data_dir() / "export" / f"{month or 'all'}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    cur = currency_of(con)
    with target.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "day", "kind", "method", "category", "amount",
                    "currency", "note", "source"])
        for r in rows:
            w.writerow([r["id"], r["day_local"], r["kind"], r["method"],
                        r["category"], f"{r['amount_cents'] / 100:.2f}", cur,
                        r["note"], r["source"]])
    return {"path": str(target), "rows": len(rows), "month": month or "all",
            "send": f"MEDIA:{target}"}


# --------------------------------------------------------------------------
# the panel: a second surface, kept current by whoever changes the ledger
# --------------------------------------------------------------------------

# Commands that change what the panel would say. `config` is in the list
# because timezone and currency decide which day a figure belongs to and how
# every number on the page is printed; `merchants --forget` is, because it
# moves spending between categories.
PANEL_REFRESH = {"add", "delete", "recategorize", "fixed", "config", "merchants",
                 "edit", "balance", "card", "budget"}


def refresh_panel(con) -> None:
    """Rewrite the wall panel, and never let that fail a ledger write.

    The ledger is the product and the panel is a view of it. If rendering
    ever breaks -- a font-sized mistake in a template, a full disk, a path
    that is not writable in some install nobody tested -- the correct
    outcome is a stale page and a logged warning, not a refused `money add`
    and an owner who is told their lunch could not be recorded.

    Two hard rules, both about the calling contract rather than the panel:
    nothing is printed to STDOUT (a skill parses that as the command's JSON),
    and nothing propagates (`add` has already committed by the time this
    runs).
    """
    try:
        import panel                             # local: panel imports money
        panel.write(con)
    except Exception as exc:                     # noqa: BLE001
        print(f"warning: panel not refreshed: {type(exc).__name__}: {exc}",
              file=sys.stderr)


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
                k.endswith("_cents") or k in {"expense", "income", "net", "total",
                                              "purchase", "card_paid"}
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
    a.add_argument("--on", default=None,
                   help="the day it happened: YYYY-MM-DD, today, yesterday,"
                        " or a weekday name (saturday, sábado). Default: now")
    a.add_argument("--via", default=None,
                   help="debit (pix, cash, boleto, debit card -- the default)"
                        " or credit (a card that settles on an invoice)")
    a.add_argument("--card", default=None,
                   help="the card's name, as a Wallet tap reports it; the"
                        " method comes from `card set`, else credit")

    e = sub.add_parser("edit", help="change one transaction in place")
    e.add_argument("id", type=int)
    e.add_argument("--on", default=None, help="move it to this day")
    e.add_argument("--amount", default=None)
    e.add_argument("--kind", choices=["expense", "income"], default=None)
    e.add_argument("--category", default=None)
    e.add_argument("--note", default=None)
    e.add_argument("--via", default=None)

    b = sub.add_parser("balance", help="what is in the account, from the last reading")
    bsub = b.add_subparsers(dest="bcmd")
    bs = bsub.add_parser("set", help="record a balance the owner read off the bank")
    bs.add_argument("amount")
    bs.add_argument("--on", default=None, help="when it was read (default: now)")

    cd = sub.add_parser("card", help="the credit card: what is open, and paying it")
    csub = cd.add_subparsers(dest="ccmd", required=True)
    cp = csub.add_parser("pay", help="record the invoice being paid")
    cp.add_argument("amount")
    cp.add_argument("--on", default=None)
    cp.add_argument("--note", default="")
    csub.add_parser("open", help="what the next invoice already holds")
    cset = csub.add_parser("set", help="what a named card is: debit or credit")
    cset.add_argument("name")
    cset.add_argument("method")
    csub.add_parser("list", help="the cards named so far")

    bu = sub.add_parser("budget", help="a monthly ceiling per category")
    busub = bu.add_subparsers(dest="bucmd")
    bset = busub.add_parser("set")
    bset.add_argument("category")
    bset.add_argument("amount")
    brm = busub.add_parser("remove")
    brm.add_argument("category")

    sub.add_parser("week", help="this week so far, against the same days last week")

    ex = sub.add_parser("export", help="the ledger as CSV, to send to the owner")
    ex.add_argument("--month", default=None, help="YYYY-MM; default: everything")

    s = sub.add_parser("summary", help="one month's totals and categories")
    s.add_argument("--month", default=None, help="YYYY-MM (default: current)")

    d1 = sub.add_parser("day", help="one day's total (default: yesterday)")
    d1.add_argument("--on", default=None, help="YYYY-MM-DD (default: yesterday)")
    # The evening brief's opening figure. A flag rather than a date the model
    # types: "today" resolved in the owner's zone is exactly the thing this
    # engine exists to keep out of the model's hands, and at 23:40 in Sao
    # Paulo the container's own date is already tomorrow.
    d1.add_argument("--today", action="store_true",
                    help="the owner's today, so far (implies partial)")

    sub.add_parser("project", help="project this month's close at the current pace")
    sub.add_parser("status", help="where this person stands and what is missing")
    up = sub.add_parser("upcoming", help="fixed lines falling due in the next days")
    up.add_argument("--days", type=int, default=7)
    u = sub.add_parser("uncategorized",
                       help="merchants the rules could not place, worst first")
    u.add_argument("--limit", type=int, default=40)
    rc = sub.add_parser("recategorize", help="apply a merchant->category map")
    rc.add_argument("--map", required=True, help="JSON, or @path to a file")

    mc = sub.add_parser("merchants", help="merchant names already learned")
    mc.add_argument("--forget", default=None,
                    help="drop one learned name, so the rules decide it again")

    sim = sub.add_parser("simulate", help="what a purchase does to the month")
    sim.add_argument("amount")
    sim.add_argument("--installments", type=int, default=1)

    r = sub.add_parser("recent", help="last transactions")
    r.add_argument("--limit", type=int, default=20)

    d = sub.add_parser("delete", help="remove a transaction by id, or the last one logged")
    d.add_argument("id", type=int, nargs="?")
    d.add_argument("--last", action="store_true",
                   help="the newest row logged in the chat -- what 'undo' means")

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
        refuse_symbol_in_amount(args.amount)
        cents = parse_amount(args.amount)
        when = resolve_on(con, args.on)
        method = (normalise_method(args.via) if args.via
                  else method_of_card(con, args.card) if args.card
                  else "debit")
        if args.source in FORWARDED:
            got = record_forwarded(
                con, cents, args.kind, args.category, args.note, args.source,
                method=normalise_method(args.via) if args.via else None,
                card=args.card, when=when)
            if got["skipped"]:
                emit(got, cur)
                return 0
            month = got["day"][:7]
            emit({**got, **{k: v for k, v in month_totals(con, month).items()
                            if k != "month"}, "month": month,
                  "card_open_cents": card_open(con)["card_open_cents"]}, cur)
            refresh_panel(con)
            return 0
        tid = add_tx(con, cents, args.kind, args.category, args.note, args.source,
                     when=when, method=method)
        stamped = get_tx(con, tid)
        month = stamped["month_local"]
        row = {"id": tid, "amount_cents": cents, "kind": args.kind,
               "method": stamped["method"], "category": args.category,
               "note": args.note, "day": stamped["day_local"],
               "backdated": stamped["day_local"] != now_local(con).strftime("%Y-%m-%d"),
               **{k: v for k, v in month_totals(con, month).items() if k != "month"},
               "month": month}
        if method == "credit" and args.kind == "expense":
            row["card_open_cents"] = card_open(con)["card_open_cents"]
        bigger = note_disagrees(args.amount, args.note)
        if bigger:
            # Written, not refused: a log that fails is worse than a log that
            # asks. The row can be deleted by id in the next call.
            row["warning"] = (
                f"recorded {args.amount}, but the note says {bigger} -- if the "
                "shell ate a `$`, delete this row and add it again with the "
                "amount in single quotes")
            row["say"] = ("check the amount with the owner in one short "
                          "question before confirming it")
        emit(row, cur)

    elif args.cmd == "summary":
        month = args.month or now_local(con).strftime("%Y-%m")
        emit({**month_totals(con, month), "categories": by_category(con, month),
              "currency": cur}, cur)

    elif args.cmd == "day":
        if args.today:
            when = now_local(con).strftime("%Y-%m-%d")
        else:
            when = args.on or yesterday_local(con)
        emit({**day_totals(con, when), "currency": cur}, cur)

    elif args.cmd == "status":
        emit({**status(con), "currency": cur}, cur)

    elif args.cmd == "upcoming":
        emit(upcoming_fixed(con, within_days=args.days), cur)

    elif args.cmd == "uncategorized":
        emit(uncategorized(con, args.limit), cur)

    elif args.cmd == "recategorize":
        raw = args.map
        payload = json.loads(Path(raw[1:]).expanduser().read_text()
                             if raw.startswith("@") else raw)
        emit(recategorize(con, payload), cur)

    elif args.cmd == "merchants":
        if args.forget:
            gone = con.execute(
                "DELETE FROM merchant_category WHERE merchant = ?",
                (" ".join(merchant_tokens(args.forget)),)).rowcount
            con.commit()
            emit({"forgot": args.forget, "removed": gone}, cur)
        else:
            rows = con.execute(
                "SELECT label, category, updated_utc FROM merchant_category"
                " ORDER BY category, label").fetchall()
            emit({"merchants": [dict(r) for r in rows], "count": len(rows)}, cur)

    elif args.cmd == "project":
        emit({**project_month(con), "currency": cur}, cur)

    elif args.cmd == "simulate":
        emit({**simulate(con, parse_amount(args.amount), args.installments),
              "currency": cur}, cur)

    elif args.cmd == "recent":
        emit(recent(con, args.limit), cur)

    elif args.cmd == "delete":
        if args.last:
            row = last_logged(con)
            if not row:
                raise SystemExit("nothing logged in the chat to undo")
            con.execute("DELETE FROM tx WHERE id = ?", (row["id"],))
            con.commit()
            emit({"deleted": row["id"], "was": row}, cur)
        elif args.id is None:
            raise SystemExit("delete needs an id, or --last for the newest row logged")
        else:
            row = get_tx(con, args.id)
            con.execute("DELETE FROM tx WHERE id = ?", (args.id,))
            con.commit()
            emit({"deleted": args.id, "was": row}, cur)

    elif args.cmd == "edit":
        if args.amount is not None:
            refuse_symbol_in_amount(args.amount)
        result = edit_tx(
            con, args.id,
            when=resolve_on(con, args.on),
            amount_cents=parse_amount(args.amount) if args.amount is not None else None,
            kind=args.kind, category=args.category, note=args.note,
            method=normalise_method(args.via) if args.via is not None else None)
        for side in ("before", "after"):
            result[side]["amount_fmt"] = fmt(result[side]["amount_cents"], cur)
        emit(result, cur)

    elif args.cmd == "balance":
        if args.bcmd == "set":
            refuse_symbol_in_amount(args.amount)
            anchor = set_balance(con, parse_amount(args.amount),
                                 when=resolve_on(con, args.on))
            emit({"anchor_cents": anchor["cents"], "anchor_day": anchor["day"],
                  **{k: v for k, v in balance(con).items() if k != "due_soon"}}, cur)
        else:
            emit({**balance(con), "currency": cur}, cur)

    elif args.cmd == "card":
        if args.ccmd == "pay":
            refuse_symbol_in_amount(args.amount)
            tid = pay_card(con, parse_amount(args.amount),
                           when=resolve_on(con, args.on), note=args.note)
            emit({"id": tid, "paid_cents": parse_amount(args.amount),
                  "kind": "transfer", "day": get_tx(con, tid)["day_local"],
                  **card_open(con),
                  "note": "not spending: settles card purchases already recorded"},
                 cur)
        elif args.ccmd == "set":
            emit(set_card(con, args.name, normalise_method(args.method)), cur)
        elif args.ccmd == "list":
            emit({"cards": cards(con), "default": "credit"}, cur)
        else:
            emit({**card_open(con), "currency": cur}, cur)

    elif args.cmd == "budget":
        if args.bucmd == "set":
            refuse_symbol_in_amount(args.amount)
            set_budget(con, args.category, parse_amount(args.amount))
        elif args.bucmd == "remove":
            remove_budget(con, args.category)
        result = budgets(con)
        for b in result["budgets"]:
            for k in ("limit_cents", "spent_cents", "left_cents", "projected_cents"):
                if b.get(k) is not None:
                    b[k.replace("_cents", "_fmt")] = fmt(b[k], cur)
        emit({**result, "currency": cur}, cur)

    elif args.cmd == "week":
        result = week(con)
        if result["top_category"]:
            result["top_category"]["total_fmt"] = fmt(
                result["top_category"]["total_cents"], cur)
        emit({**result, "currency": cur}, cur)

    elif args.cmd == "export":
        emit(export_csv(con, args.month), cur)

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
            stored = validate_cfg(args.key, args.value)
            set_cfg(con, args.key, stored)
            emit({args.key: stored})
        elif args.key:
            emit({args.key: get_cfg(con, args.key)})
        else:
            rows = con.execute("SELECT key, value FROM config ORDER BY key").fetchall()
            emit({r["key"]: r["value"] for r in rows})

    # Last, and after emit(): the ledger has already answered by the time the
    # second surface is redrawn, so a slow or broken panel cannot delay -- or
    # corrupt -- the JSON a skill is waiting on.
    if args.cmd in PANEL_REFRESH:
        refresh_panel(con)
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
        # `say` belongs on THIS branch above all. A SystemExit is the
        # ordinary, expected failure -- a file that will not parse, a mapping
        # that is wrong -- so it is the envelope the agent actually meets,
        # and it was the one branch that shipped without the instruction.
        # The owner got `{"error": "no transaction lines found -- this file
        # has no rows that start with a date...", "ok": false}` pasted at
        # them, verbatim, as the answer to "import my card statements".
        print(json.dumps({
            "error": str(exc.code),
            "ok": False,
            "say": "tell the owner this in one sentence, in their language; never paste this object at them",
        }, ensure_ascii=False))
        return 1
    except Exception as exc:                     # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "ok": False,
            "say": "tell the owner in one sentence what did not work, in "
                   "their language; never paste this object at them",
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(_run(main, None))
