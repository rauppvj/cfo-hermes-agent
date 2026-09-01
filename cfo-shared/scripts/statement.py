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
import os
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

def pdf_to_text(path: Path, password: str | None = None) -> str:
    """Extract a PDF's text, in the container, via uv.

    The image carries no PDF library and no pip -- but it does carry uv, and
    the agent found that on its own the first time it met a PDF. Better to
    make it the tool's own path than to leave the agent improvising a
    one-liner whose traceback lands on someone's phone.
    """
    import subprocess
    helper = Path(__file__).resolve().parent / "pdf_text.py"
    env = dict(os.environ)
    if password:
        env["CFO_PDF_PASSWORD"] = password       # env, never argv
    # --no-project and a neutral cwd, both deliberate: the agent's working
    # directory is /opt/hermes, which holds the gateway's own pyproject.toml.
    # Without these, `uv run` tries to build hermes-agent as an editable
    # install and dies on a directory the agent's uid cannot write -- an
    # error about egg-info timestamps that says nothing about PDFs at all.
    proc = subprocess.run(
        ["uv", "run", "--quiet", "--no-project", "--with", "pypdf",
         "python3", str(helper), str(path)],
        capture_output=True, text=True, env=env, timeout=180, cwd="/tmp")
    if proc.returncode == 0:
        return proc.stdout
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {}
    if not payload.get("error"):
        # An empty payload once swallowed the real cause entirely, and the
        # agent told the owner "I could not read this PDF" about a file that
        # was merely locked. Never lose the reason.
        payload["error"] = ((proc.stderr or "").strip()[-300:]
                            or "could not read the PDF")
    raise PdfLocked(payload) if payload.get("needs_password") else SystemExit(
        payload.get("error", "could not read the PDF"))


class PdfLocked(Exception):
    def __init__(self, payload):
        super().__init__(payload.get("error", "password required"))
        self.payload = payload


def read_text(path: Path, password: str | None = None) -> tuple[str, str]:
    if path.suffix.lower() == ".pdf":
        return pdf_to_text(path, password), "pdf"
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


def sniff(path: Path, password: str | None = None) -> dict:
    """What the model needs to propose a mapping, and nothing else.

    Deliberately a SAMPLE, not the file: the point of this design is that the
    statement's hundreds of rows never enter a prompt.
    """
    text, encoding = read_text(path, password)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise SystemExit("statement is empty")

    if not looks_like_csv(text):
        hits = parse_text_lines(text)
        if not hits:
            raise SystemExit(
                "no transaction lines found. If this came from a PDF, convert "
                "it on the Mac first: pdftotext -layout [-upw PASSWORD] "
                "file.pdf out.txt")
        two = sum(1 for h in hits if len(h["amounts"]) > 1)
        return {
            "path": str(path), "encoding": encoding, "format": "text",
            "total_rows": len(hits),
            "second_amount_is_balance": two > len(hits) / 2,
            "sample": hits[:SAMPLE_ROWS],
            "next": ("confirm the mapping: {\"format\":\"text\", \"sign\":...}. "
                     "Where two amounts are on a line the first is the "
                     "transaction and the second the running balance."),
        }

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


# A statement that arrived as a PDF is a RENDERING, not data: by the time it
# is text, the columns are whitespace and the only reliable landmarks are a
# date at the start of a line and money at the end. That is what this matches.
# Everything else on the line -- balances, document numbers, branch codes --
# is between them.
# The sign may sit apart from the digits ("-     141,37"): a PDF right-aligns
# the number inside a column and the minus stays at the column's left edge.
# It may also trail ("141,37-"), or be a D/C marker, which some banks use
# instead of a sign at all.
MONEY = (r"[-+]?\s{0,8}(?:R\$|\$|US\$|€|£)?\s?"
         r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?\s?[-DC]?")
DATE_TOKEN = r"(?:\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?|\d{4}-\d{2}-\d{2})"
TEXT_LINE = re.compile(
    rf"^\s*(?P<date>{DATE_TOKEN})\s+(?P<rest>\S.*?)\s+"
    rf"(?P<amounts>(?:{MONEY})(?:\s+{MONEY})?)\s*$")


def parse_text_lines(text: str) -> list[dict]:
    """Transaction-looking lines from a PDF that has been through pdftotext."""
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        mo = TEXT_LINE.match(line.rstrip())
        if not mo:
            continue
        amounts = re.findall(MONEY, mo.group("amounts"))
        amounts = [a for a in (a.strip() for a in amounts) if a]
        if not amounts:
            continue
        out.append({"line": i, "date": mo.group("date"),
                    "description": mo.group("rest").strip(),
                    "amounts": amounts})
    return out


def looks_like_csv(text: str) -> bool:
    """Tell a delimited file from a de-PDF'd layout.

    Counting commas does not work, and neither does counting them
    *consistently*: "-1.800,00   3.200,00" carries two commas on every line of
    a statement, because money punctuation is the same punctuation and a
    column layout is regular by definition. Both naive tests call this CSV.

    What actually separates them is the WHITESPACE. A layout aligns columns by
    padding, so almost every line holds a run of three or more spaces; a CSV
    delimits with a character and has none. So wide gaps win, and a real
    delimiter only decides the cases without them.
    """
    lines = [l for l in text.splitlines() if l.strip()][:20]
    if len(lines) < 3:
        return False

    wide = sum(1 for l in lines if re.search(r"\S {3,}\S", l))
    if wide >= len(lines) * 0.6:
        return False

    for d in [";", "\t", "|", ","]:
        steady = [l.count(d) for l in lines if l.count(d) >= 2]
        if len(steady) >= len(lines) * 0.7 and len(set(steady)) == 1:
            return True
    return False


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

def extract(path: Path, mapping: dict, password: str | None = None) -> tuple[list[dict], list[dict]]:
    """Every row in the file, normalised. No sampling, no model, no guessing.

    `mapping` names the columns and the sign convention:

        {"date": "Data", "amount": "Valor", "description": "Descrição",
         "sign": "negative_is_expense" | "positive_is_expense",
         "debit": "Débito", "credit": "Crédito",   # instead of one amount
         "date_format": "%d/%m/%Y"}                # optional hint
    """
    info = sniff(path, password)
    if info.get("format") == "text" or mapping.get("format") == "text":
        return extract_text(path, mapping, password)
    text, _ = read_text(path, password)
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


def normalise_sign(raw: str) -> str:
    """Bring a detached, trailing or lettered sign back onto the number."""
    s = raw.strip()
    negative = False
    if s.endswith(("-", "D")):        # "141,37-" / "141,37 D" (debito)
        negative, s = s[-1] == "-" or s[-1] == "D", s[:-1].strip()
    elif s.endswith("C"):             # credito
        s = s[:-1].strip()
    if s.startswith(("-", "+")):
        negative = negative or s[0] == "-"
        s = s[1:].strip()
    return ("-" if negative else "") + s


def extract_text(path: Path, mapping: dict, password: str | None = None) -> tuple[list[dict], list[dict]]:
    """The same normalised rows, from a de-PDF'd statement."""
    text, _ = read_text(path, password)
    sign = mapping.get("sign", "negative_is_expense")
    date_format = mapping.get("date_format")
    rows, rejected = [], []

    for hit in parse_text_lines(text):
        try:
            cents = m.parse_amount(normalise_sign(hit["amounts"][0]))
            kind = "expense" if (
                (cents < 0) == (sign == "negative_is_expense")) else "income"
            cents = abs(cents)
            if cents == 0:
                continue
            day = parse_date(hit["date"], date_format)
            description = hit["description"]
            rows.append({
                "day": day, "amount_cents": cents, "kind": kind,
                "description": description,
                "category": categorize(description),
                "hash": row_hash(day, cents if kind == "expense" else -cents,
                                 description),
            })
        except ValueError as exc:
            rejected.append({"line": hit["line"], "reason": str(exc),
                             "raw": hit["description"][:40]})
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
          batch: str | None = None, password: str | None = None) -> dict:
    rows, rejected = extract(path, mapping, password)
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

class _Parser(argparse.ArgumentParser):
    """argparse exits with a usage string on stderr and code 2. That string is
    what reached someone's phone as an answer. Raising instead lets _run turn
    it into a readable JSON error carrying the valid choices."""

    def error(self, message):
        raise ValueError(f"{message}. try: {self.prog} --help")

def main(argv=None) -> int:
    p = _Parser(
        prog="statement", description="import a bank or card statement")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    i = sub.add_parser("inspect", help="headers and a sample, to build a mapping")
    i.add_argument("file")
    i.add_argument("--password", default=None,
                   help="for a locked PDF; used once and never stored")

    a = sub.add_parser("apply", help="import rows using a column mapping")
    a.add_argument("file")
    a.add_argument("--map", required=True, help="JSON, or @path to a JSON file")
    a.add_argument("--password", default=None,
                   help="for a locked PDF; used once and never stored")
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
        m.emit(sniff(Path(args.file).expanduser(), args.password), cur)
    elif args.cmd == "apply":
        raw = args.map
        mapping = json.loads(Path(raw[1:]).expanduser().read_text()
                             if raw.startswith("@") else raw)
        m.emit(apply(con, Path(args.file).expanduser(), mapping,
                     dry_run=not args.commit, password=args.password), cur)
    elif args.cmd == "undo":
        m.emit(undo(con, args.batch), cur)
    elif args.cmd == "batches":
        m.emit(batches(con), cur)
    elif args.cmd == "detect":
        m.emit(detect_recurring(con), cur)
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
    except PdfLocked as exc:
        print(json.dumps({**exc.payload, "ok": False}, ensure_ascii=False))
        return 2
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
