#!/usr/bin/env python3
"""Import a bank or card statement, from any bank, in any country.

The split that makes this work in a country nobody wrote a parser for:

    the model infers the COLUMN MAPPING; the code reads every ROW.

Asking a model to transcribe three hundred transactions is slow, expensive,
and puts a hallucinated amount one token away from someone's ledger. Asking it
which column holds the date is a small, reliable question it answers from a
five-line sample. So `inspect` shows it the shape of the file, it proposes a
mapping, and `apply` does all the reading, parsing, hashing and arithmetic
from that mapping alone.

That is why there is no Nubank parser here, and no Chase one. There is no bank
list at all -- which is the point, because a statement importer that only
knows one country's banks is one almost nobody can use.

Three rules the file exists to enforce:

  * NOTHING LANDS WITHOUT A DRY RUN. Ninety days is hundreds of rows; an
    import that goes in silently wrong poisons the ledger, and every number
    the agent says afterwards is wrong with total confidence.
  * IMPORTING TWICE IS FREE. Rows are hashed on (day, amount, description),
    so re-importing an overlapping statement -- which people do, because they
    forget -- adds only what is new.
  * EVERY IMPORT IS ONE BATCH, AND A BATCH CAN BE UNDONE. `source` carries
    the batch id, so a bad import is one command to reverse, not a night of
    hand-deleting rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import money as m  # noqa: E402

SAMPLE_ROWS = 6
ENCODINGS = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]

# Keyword rules, deliberately multilingual: a statement is in the owner's
# language, and the agent is used in more than one country. Anything unmatched
# stays `other` -- a wrong category is worse than an honest one, because it is
# invisible in a summary.
RULES = [
    ("groceries", ["supermerc", "mercado", "grocer", "atacad", "hortifrut",
                   "carrefour", "walmart", "aldi", "tesco", "assai",
                   "whole foods", "trader joe", "kroger", "safeway", "costco",
                   "publix", "lidl", "sam's club", "pao de acucar", "sendas",
                   "big bompreco", "zaffari", "angeloni"]),
    ("food", ["restaurant", "ifood", "rappi", "uber eats", "doordash", "lanche",
              "padaria", "bakery", "cafe", "café", "coffee", "starbucks",
              "mcdonald", "burger", "pizza", "bar ", "delivery"]),
    ("transport", ["uber", "99app", "99 ", "lyft", "posto", "shell", "ipiranga",
                   "combustivel", "fuel", "gas station", "metro", "estacion",
                   "parking", "pedagio", "taxi", "cabify"]),
    ("subscriptions", ["netflix", "spotify", "disney", "hbo", "prime video",
                       "youtube premium", "icloud", "google one", "dropbox",
                       "assinatura", "subscription", "adobe", "notion"]),
    ("utilities", ["energia", "eletric", "electric", "agua", "água", "water",
                   "gas natural", "internet", "vivo", "claro", "tim ", "oi ",
                   "telefon", "comcast", "verizon"]),
    ("housing", ["aluguel", "rent", "condominio", "condomínio", "mortgage",
                 "iptu", "hoa "]),
    ("health", ["farmacia", "farmácia", "drogaria", "pharmacy", "cvs",
                "walgreens", "hospital", "clinica", "clínica", "dentist",
                "medic", "unimed", "plano de saude"]),
    ("education", ["escola", "faculdade", "universidade", "curso", "udemy",
                   "coursera", "school", "tuition", "alura"]),
    ("leisure", ["cinema", "cinemark", "netflix film", "show", "ingresso",
                 "steam", "playstation", "xbox", "nintendo", "spotify live",
                 "academia", "gym", "smartfit"]),
    ("fees", ["tarifa", "juros", "iof", "anuidade", "fee", "interest",
              "service charge", "multa"]),
]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def categorize(description: str) -> str:
    d = strip_accents(description or "").lower()
    for category, needles in RULES:
        for n in needles:
            if strip_accents(n) in d:
                return category
    return "other"


# --------------------------------------------------------------------------
# reading the file
# --------------------------------------------------------------------------

def read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


def sniff(path: Path) -> dict:
    """What the model needs to propose a mapping, and nothing else.

    Deliberately a SAMPLE, not the file: the point of this design is that the
    statement's hundreds of rows never enter a prompt.
    """
    text, encoding = read_text(path)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise SystemExit("statement is empty")

    # Some banks put a title block above the header; the header is the first
    # line whose delimiter count matches the lines that follow it.
    delimiter, header_index = ",", 0
    best = -1
    for cand in [",", ";", "\t", "|"]:
        for i, line in enumerate(lines[:12]):
            n = line.count(cand)
            if n < 1:
                continue
            following = [l.count(cand) for l in lines[i + 1:i + 6]]
            if following and all(f == n for f in following) and n > best:
                delimiter, header_index, best = cand, i, n

    body = "\n".join(lines[header_index:])
    reader = csv.reader(io.StringIO(body), delimiter=delimiter)
    rows = list(reader)
    headers = rows[0] if rows else []

    return {
        "path": str(path),
        "encoding": encoding,
        "delimiter": delimiter,
        "header_line": header_index + 1,
        "headers": headers,
        "total_rows": max(0, len(rows) - 1),
        "sample": [dict(zip(headers, r)) for r in rows[1:SAMPLE_ROWS + 1]],
        "next": ("propose a mapping: which header holds the date, the amount, "
                 "the description; and whether an expense is a negative amount "
                 "or lives in its own debit column"),
    }


DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%Y/%m/%d", "%d/%m/%y", "%m/%d/%y", "%b %d, %Y", "%d %b %Y"]


def parse_date(raw: str, preferred: str | None = None) -> str:
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty date")
    formats = ([preferred] if preferred else []) + DATE_FORMATS
    for f in formats:
        if not f:
            continue
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # ISO timestamps: keep the date half.
    mo = re.match(r"(\d{4}-\d{2}-\d{2})[T ]", s)
    if mo:
        return mo.group(1)
    raise ValueError(f"unreadable date: {raw!r}")


def row_hash(day: str, cents: int, description: str) -> str:
    key = f"{day}|{cents}|{strip_accents(description).lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def extract(path: Path, mapping: dict) -> tuple[list[dict], list[dict]]:
    """Every row in the file, normalised. No sampling, no model, no guessing.

    `mapping` names the columns and the sign convention:

        {"date": "Data", "amount": "Valor", "description": "Descrição",
         "sign": "negative_is_expense" | "positive_is_expense",
         "debit": "Débito", "credit": "Crédito",   # instead of one amount
         "date_format": "%d/%m/%Y"}                # optional hint
    """
    info = sniff(path)
    text, _ = read_text(path)
    lines = [l for l in text.splitlines() if l.strip()]
    body = "\n".join(lines[info["header_line"] - 1:])
    reader = csv.DictReader(io.StringIO(body), delimiter=info["delimiter"])

    date_col = mapping.get("date")
    amount_col = mapping.get("amount")
    debit_col, credit_col = mapping.get("debit"), mapping.get("credit")
    desc_col = mapping.get("description")
    sign = mapping.get("sign", "negative_is_expense")
    date_format = mapping.get("date_format")

    if not date_col or not (amount_col or debit_col or credit_col):
        raise SystemExit("mapping needs at least a date column and an amount "
                         "(or a debit/credit pair)")

    rows, rejected = [], []
    for i, raw in enumerate(reader, start=2):
        try:
            day = parse_date(raw.get(date_col, ""), date_format)
            description = (raw.get(desc_col, "") or "").strip() if desc_col else ""

            if amount_col:
                cents = m.parse_amount(raw.get(amount_col, ""))
                kind = "expense" if (
                    (cents < 0) == (sign == "negative_is_expense")) else "income"
                cents = abs(cents)
            else:
                d = (raw.get(debit_col, "") or "").strip() if debit_col else ""
                c = (raw.get(credit_col, "") or "").strip() if credit_col else ""
                if d:
                    cents, kind = abs(m.parse_amount(d)), "expense"
                elif c:
                    cents, kind = abs(m.parse_amount(c)), "income"
                else:
                    continue  # a blank row between sections
            if cents == 0:
                continue

            rows.append({
                "day": day, "amount_cents": cents, "kind": kind,
                "description": description,
                "category": categorize(description),
                "hash": row_hash(day, cents if kind == "expense" else -cents,
                                 description),
            })
        except (ValueError, KeyError) as exc:
            rejected.append({"line": i, "reason": str(exc),
                             "raw": {k: v for k, v in list(raw.items())[:4]}})
    return rows, rejected


def existing_hashes(con) -> set[str]:
    cur = con.execute("SELECT note FROM tx WHERE source LIKE 'import:%'")
    out = set()
    for r in cur:
        mo = re.search(r"#([0-9a-f]{16})$", r["note"] or "")
        if mo:
            out.add(mo.group(1))
    return out


def apply(con, path: Path, mapping: dict, dry_run: bool = True,
          batch: str | None = None) -> dict:
    rows, rejected = extract(path, mapping)
    if not rows:
        raise SystemExit("no readable transactions -- check the mapping")

    seen = existing_hashes(con)
    fresh, duplicates = [], 0
    batch_seen = set()
    for r in rows:
        if r["hash"] in seen or r["hash"] in batch_seen:
            duplicates += 1
            continue
        batch_seen.add(r["hash"])
        fresh.append(r)

    days = sorted({r["day"] for r in rows})
    by_cat: dict[str, int] = {}
    total_expense = total_income = 0
    for r in fresh:
        if r["kind"] == "expense":
            total_expense += r["amount_cents"]
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amount_cents"]
        else:
            total_income += r["amount_cents"]

    batch = batch or datetime.now().strftime("%Y%m%d%H%M%S")
    summary = {
        "dry_run": dry_run,
        "batch": batch,
        "file": path.name,
        "rows_in_file": len(rows),
        "already_present": duplicates,
        "to_import": len(fresh),
        "unreadable": len(rejected),
        "rejected_sample": rejected[:3],
        "period": {"from": days[0], "to": days[-1]} if days else None,
        "expense": total_expense,
        "income": total_income,
        "by_category": sorted(
            ({"category": k, "total": v} for k, v in by_cat.items()),
            key=lambda x: -x["total"]),
        "sample": [
            {"day": r["day"], "amount_cents": r["amount_cents"],
             "kind": r["kind"], "category": r["category"],
             "description": r["description"][:48]}
            for r in fresh[:5]
        ],
    }

    if dry_run:
        summary["next"] = ("show this to the owner and get a yes before "
                           "running again with --commit")
        return summary

    tz = m.tz_of(con)
    for r in fresh:
        when = datetime.strptime(r["day"], "%Y-%m-%d").replace(
            hour=12, tzinfo=tz)  # midday: never straddles a zone boundary
        m.add_tx(con, r["amount_cents"], r["kind"], r["category"],
                 note=f"{r['description']} #{r['hash']}",
                 source=f"import:{batch}", when=when)
    summary["imported"] = len(fresh)
    summary["undo"] = f"statement.py undo {batch}"
    return summary


def undo(con, batch: str) -> dict:
    n = con.execute("DELETE FROM tx WHERE source = ?", (f"import:{batch}",)).rowcount
    con.commit()
    return {"batch": batch, "removed": n}


def batches(con) -> list[dict]:
    rows = con.execute(
        "SELECT source, COUNT(*) AS n, MIN(day_local) AS f, MAX(day_local) AS t"
        " FROM tx WHERE source LIKE 'import:%' GROUP BY source"
        " ORDER BY source DESC").fetchall()
    return [{"batch": r["source"].split(":", 1)[1], "transactions": r["n"],
             "from": r["f"], "to": r["t"]} for r in rows]


# --------------------------------------------------------------------------
# what the statement already told us about this person
# --------------------------------------------------------------------------

def detect_recurring(con, min_occurrences: int = 3) -> dict:
    """Find the salary and the fixed bills, so nobody is asked what the data
    already says.

    This is the half of setup that should never have been a question: someone
    who just imported ninety days has already told the agent what they earn
    and when. Candidates are PROPOSED, never written -- a misread salary is
    the one error that silently inverts every projection, so a human confirms.
    """
    rows = con.execute(
        "SELECT day_local, amount_cents, kind, note FROM tx"
        " WHERE source LIKE 'import:%' ORDER BY day_local").fetchall()

    groups: dict[tuple, list] = {}
    for r in rows:
        label = re.sub(r"\s*#[0-9a-f]{16}$", "", r["note"] or "").strip()
        # Bills drift by a few cents (FX, usage). Bucket to the nearest real.
        bucket = round(r["amount_cents"], -2)
        key = (r["kind"], bucket, strip_accents(label).lower()[:18])
        groups.setdefault(key, []).append((r["day_local"], r["amount_cents"], label))

    out = []
    for (kind, _bucket, _prefix), hits in groups.items():
        if len(hits) < min_occurrences:
            continue
        days = [datetime.strptime(h[0], "%Y-%m-%d") for h in hits]
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        if not gaps:
            continue
        avg = sum(gaps) / len(gaps)
        spread = max(gaps) - min(gaps)

        if 26 <= avg <= 35 and spread <= 6:
            frequency, day_of_month = "monthly", days[-1].day
        elif 6 <= avg <= 8 and spread <= 3:
            frequency, day_of_month = "weekly", days[-1].isoweekday()
        elif 13 <= avg <= 16 and spread <= 4:
            frequency, day_of_month = "biweekly", days[-1].day
        else:
            continue

        amounts = [h[1] for h in hits]
        out.append({
            "label": hits[-1][2][:40] or "recurring",
            "kind": kind,
            "amount_cents": max(set(amounts), key=amounts.count),
            "frequency": frequency,
            "day": day_of_month,
            "occurrences": len(hits),
            "confidence": "high" if spread <= 2 else "medium",
            "command": None,  # filled below
        })

    for c in out:
        c["command"] = (
            f'money.py fixed add "{c["label"]}" '
            f'{c["amount_cents"] / 100:.2f} --kind {c["kind"]} '
            f'--every {c["frequency"]} --day {c["day"]}')

    income = [c for c in out if c["kind"] == "income"]
    expense = [c for c in out if c["kind"] == "expense"]
    income.sort(key=lambda c: -c["amount_cents"])
    expense.sort(key=lambda c: -c["amount_cents"])

    return {
        "income_candidates": income,
        "fixed_expense_candidates": expense,
        "next": ("confirm each with the owner before writing it -- a misread "
                 "salary inverts every projection"),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="statement", description="import a bank or card statement")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("inspect", help="headers and a sample, to build a mapping")
    i.add_argument("file")

    a = sub.add_parser("apply", help="import rows using a column mapping")
    a.add_argument("file")
    a.add_argument("--map", required=True, help="JSON, or @path to a JSON file")
    a.add_argument("--commit", action="store_true",
                   help="actually write; without it this is a dry run")

    u = sub.add_parser("undo", help="remove everything one import wrote")
    u.add_argument("batch")

    sub.add_parser("batches", help="list past imports")
    sub.add_parser("detect", help="propose fixed lines found in imported data")

    args = p.parse_args(argv)
    con = m.connect()
    cur = m.currency_of(con)

    if args.cmd == "inspect":
        m.emit(sniff(Path(args.file).expanduser()), cur)
    elif args.cmd == "apply":
        raw = args.map
        mapping = json.loads(Path(raw[1:]).expanduser().read_text()
                             if raw.startswith("@") else raw)
        m.emit(apply(con, Path(args.file).expanduser(), mapping,
                     dry_run=not args.commit), cur)
    elif args.cmd == "undo":
        m.emit(undo(con, args.batch), cur)
    elif args.cmd == "batches":
        m.emit(batches(con), cur)
    elif args.cmd == "detect":
        m.emit(detect_recurring(con), cur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
