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


# One definition, in money.py, because the importer and the merchant matcher
# have to fold a name the same way or a merchant learned from a statement
# stops matching the statement it came from.
strip_accents = m.strip_accents


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


NBSP = dict.fromkeys(map(ord, "\u00a0\u2007\u202f\u2009\u2002\u2003"), " ")


def read_text(path: Path, password: str | None = None) -> tuple[str, str]:
    if path.suffix.lower() == ".pdf":
        # PDFs are full of typographic spaces -- a non-breaking one inside
        # "R$ 1.234,56" is invisible and breaks every pattern that expects a
        # plain space. Flatten them once, here, so nothing downstream has to
        # know.
        return pdf_to_text(path, password).translate(NBSP), "pdf"
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
        if looks_like_card_invoice(text):
            closing = card_closing_date(text)
            hits = parse_card_lines(text, closing)
            spend = [h for h in hits if not h["credit"]]
            credits = [h for h in hits if h["credit"]]
            return {
                "path": str(path), "encoding": encoding,
                "format": "card_invoice",
                "total_rows": len(hits),
                "purchases": len(spend),
                "closing_date": closing.isoformat() if closing else None,
                "credits_excluded": [
                    {"description": h["description"], "amount": h["amounts"][0],
                     "why": h["credit"]} for h in credits],
                "sample": [
                    {"date": h["date"], "description": h["description"],
                     "amount": h["amounts"][0]} for h in spend[:SAMPLE_ROWS]],
                "next": (
                    "a credit-card invoice: no mapping is needed, apply it with "
                    "{\"format\":\"card_invoice\"}. Every row is a purchase in "
                    "the account currency. Payments of the previous invoice and "
                    "refunds are listed in credits_excluded and are NOT imported "
                    "-- a payment is settling spending, not spending."
                    if closing else
                    "the invoice does not print the year of its rows and no "
                    "closing date was found. Ask the owner which month this "
                    "invoice closed and pass it as "
                    "{\"format\":\"card_invoice\",\"closing\":\"YYYY-MM-DD\"} "
                    "-- do not guess it"),
            }

        hits = parse_text_lines(text)
        if not hits:
            raise SystemExit(
                "no transaction lines found -- this file has no rows that "
                "start with a date and end with an amount. Check it is the "
                "statement itself and not a summary page, or ask the bank "
                "for a CSV or OFX export.")
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


MONTHS = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "enero": 1, "febrero": 2, "marzo": 3, "mayo": 5,
    "junio": 6, "julio": 7, "septiembre": 9, "octubre": 10, "diciembre": 12,
}
FULL_DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")
MONTH_YEAR = re.compile(r"\b([A-Za-zçÇãÃéÉ]{4,12})\s+(\d{4})\b")
LEADING_DATE = re.compile(rf"^\s*{DATE_TOKEN}\s+")


def year_context(lines: list[str]) -> list[int | None]:
    """The year in force at each line.

    Statements print `07/06` in the rows and put the year once, in the
    section header above them -- "Junho 2026 ( 01/06/2026 - 30/06/2026 )".
    Assuming the current year instead is wrong for every statement that
    spans a December, and silently: the rows import, just into the wrong
    year, where they vanish from every total.
    """
    years: list[int | None] = []
    current: int | None = None
    for line in lines:
        mo = FULL_DATE.search(line)
        if mo:
            current = int(mo.group(3))
        else:
            mo = MONTH_YEAR.search(line)
            if mo and strip_accents(mo.group(1)).lower() in MONTHS:
                current = int(mo.group(2))
        years.append(current)
    return years


# Statements prefix every row with a transaction TYPE -- "Pagamento",
# "Saida PIX Pix enviado para", "Debit Card Purchase". It is the same handful
# of words on hundreds of rows: it buries the merchant name, defeats keyword
# matching, and makes two unrelated bills look alike to the recurrence
# detector. Strip it; the direction is already known from the sign.
TYPE_PREFIXES = [
    "entrada pix pix recebido de", "saida pix pix enviado para",
    "entrada pix", "saida pix", "pix recebido de", "pix enviado para",
    "debito de cartao", "credito de cartao", "compra no debito",
    "compra no credito", "pagamento de boleto", "pagamento", "recebimento",
    "transferencia recebida de", "transferencia enviada para",
    "transferencia", "deposito", "saque", "outros gastos", "outras entradas",
    "debit card purchase", "card purchase", "purchase authorized on",
    "direct deposit", "ach debit", "ach credit", "pos debit", "withdrawal",
    "deposit", "payment to", "payment from", "transfer to", "transfer from",
]


def clean_description(raw: str) -> str:
    """Drop the transaction-type boilerplate, keep the counterparty."""
    text = " ".join(raw.split())
    flat = strip_accents(text).lower()
    for prefix in TYPE_PREFIXES:
        if flat.startswith(prefix):
            trimmed = text[len(prefix):].strip(" -:*")
            return trimmed or text
    return text


def parse_text_lines(text: str) -> list[dict]:
    """Transaction-looking lines from a statement that was a PDF."""
    raw_lines = text.splitlines()
    years = year_context(raw_lines)
    out = []
    for i, line in enumerate(raw_lines, start=1):
        mo = TEXT_LINE.match(line.rstrip())
        if not mo:
            continue
        amounts = re.findall(MONEY, mo.group("amounts"))
        amounts = [a for a in (a.strip() for a in amounts) if a]
        if not amounts:
            continue
        # Many statements carry two dates -- posted and settled. The first
        # is the transaction's; the second leads the description and would
        # otherwise be filed as part of the merchant's name.
        description = clean_description(
            LEADING_DATE.sub("", mo.group("rest")))

        out.append({"line": i, "date": mo.group("date"),
                    "description": description,
                    "year": years[i - 1],
                    "amounts": amounts})
    return out



# --------------------------------------------------------------------------
# credit-card invoices
# --------------------------------------------------------------------------
#
# A fatura is not a statement, and every assumption the statement parser makes
# is wrong here. Rows carry a day and an abbreviated month with NO year
# anywhere on the line. The amount is glued to the description with no
# separator. A foreign purchase prints three numbers -- the charge in reais,
# the original in its own currency, and the exchange rate -- and the rate is
# LAST, so "the amount at the end of the line" reads the rate as the
# transaction. And the invoice's own payment of the previous month sits in the
# middle of the rows with no minus sign in front of it.

MONTH_ABBR = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "mai": 5, "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8,
    "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11,
    "dez": 12, "dic": 12, "dec": 12,
}

# Two decimals, always: it is what separates money from the account digits and
# card suffixes that share a line ("C6 Platinum Final 2763").
CARD_MONEY = r"\d{1,3}(?:\.\d{3})*,\d{2}"
CARD_LINE = re.compile(
    rf"^\s*(?P<day>\d{{1,2}})\s+(?P<mon>[A-Za-z\u00e7]{{3,4}})\.?\s+"
    rf"(?P<rest>\S.*?)\s*(?<![\d.,])(?P<amount>{CARD_MONEY})"
    rf"(?P<tail>\D.*)?$")

# The closing date, never the due date: the due date is a month later, and
# using it rolls every December purchase into the wrong year.
CARD_CLOSING = re.compile(
    r"(?:fechamento|fechada|ate|closing|through|statement date)"
    r"[^0-9\n]{0,40}(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})",
    re.IGNORECASE)
ANY_FULL_DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")

CARD_CREDITS = [
    ("payment", ["inclusao de pagamento", "pagamento recebido",
                 "pagamento efetuado", "pagamento da fatura", "pagamento de fatura",
                 "payment received", "payment - thank you", "pgto fatura"]),
    ("refund", ["estorno", "devolucao", "reembolso", "cashback", "refund",
                "credito de ajuste"]),
]


def _as_date(day: int, month: int, year: int):
    year += 2000 if year < 100 else 0
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def card_closing_date(text: str, override: str | None = None):
    """The day the invoice closed, which is the only year the rows have.

    Rows say "29 abr" and nothing else. Assuming the current year is wrong for
    every invoice read in January about December, and wrong silently -- the
    rows import, into a year where they are in no total the owner ever looks
    at.
    """
    if override:
        return datetime.strptime(override, "%Y-%m-%d").date()

    flat = strip_accents(text)
    named = [d for d in (_as_date(int(mo.group(1)), int(mo.group(2)),
                                  int(mo.group(3)))
                         for mo in CARD_CLOSING.finditer(flat)) if d]
    if named:
        return min(named)
    printed = [d for d in (_as_date(int(mo.group(1)), int(mo.group(2)),
                                    int(mo.group(3)))
                           for mo in ANY_FULL_DATE.finditer(flat)) if d]
    return min(printed) if printed else None


def card_credit_kind(description: str) -> str | None:
    flat = strip_accents(description).lower()
    for kind, needles in CARD_CREDITS:
        if any(n in flat for n in needles):
            return kind
    return None


def parse_card_lines(text: str, closing=None) -> list[dict]:
    """Rows from a credit-card invoice, with the year resolved from closing."""
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        mo = CARD_LINE.match(line.rstrip())
        if not mo:
            continue
        month = MONTH_ABBR.get(strip_accents(mo.group("mon")).lower()[:3])
        if not month:
            continue
        day = int(mo.group("day"))
        year = None
        if closing:
            # Everything on an invoice happened before it closed, so a month
            # after the closing month belongs to the year before.
            year = closing.year if month <= closing.month else closing.year - 1
        when = _as_date(day, month, year) if year else None
        if year and not when:
            continue

        description = clean_description(mo.group("rest"))
        tail = (mo.group("tail") or "").strip()
        if "iof" in strip_accents(tail).lower()[:4]:
            # A real charge, on the same day and merchant as the purchase it
            # taxes. Marked so two rows for one shop are not a mystery.
            description = f"{description} IOF"

        out.append({
            "line": i, "day": day, "month": month,
            "date": when.isoformat() if when else None,
            "description": description,
            "amounts": [mo.group("amount")],
            "credit": card_credit_kind(description),
        })
    return out


def looks_like_card_invoice(text: str) -> bool:
    return len(parse_card_lines(text)) > len(parse_text_lines(text))


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


def parse_date(raw: str, preferred: str | None = None,
               year: int | None = None) -> str:
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

    # A day and a month with no year, which is what a statement prints in its
    # rows. The year comes from the section header above them.
    mo = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})", s)
    if mo and year:
        d, mth = int(mo.group(1)), int(mo.group(2))
        if preferred and preferred.startswith("%m"):
            d, mth = mth, d
        return f"{year:04d}-{mth:02d}-{d:02d}"

    raise ValueError(f"unreadable date: {raw!r}"
                     + ("" if year else " (no year on the line, and no"
                                        " section header gave one)"))


def row_hash(day: str, cents: int, description: str, seq: int = 0) -> str:
    """Identify a row so the same file cannot import twice.

    `seq` is which occurrence this is of an otherwise identical row, and it is
    the difference between deduplicating a file and deleting a person's day.
    Four R$ 3,00 bus fares on one afternoon are four separate rides that share
    a date, an amount and a merchant -- on this owner's June invoice exactly
    that happened, and collapsing them dropped R$ 9,00 while the import
    reported success. Counting the repeats keeps re-importing the same file
    free (the same rows come back in the same order, so the same sequence) and
    keeps a repeated purchase a purchase.

    Seq 0 is written without the suffix, so every hash already in a ledger
    stays what it was.
    """
    key = f"{day}|{cents}|{strip_accents(description).lower().strip()}"
    if seq:
        key = f"{key}|{seq}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def stamp_hashes(rows: list[dict]) -> list[dict]:
    """Give every row its hash, numbering repeats in the order they appear."""
    seen: dict[tuple, int] = {}
    for r in rows:
        signed = r["amount_cents"] if r["kind"] == "expense" else -r["amount_cents"]
        key = (r["day"], signed, strip_accents(r["description"]).lower().strip())
        seq = seen.get(key, 0)
        seen[key] = seq + 1
        r["hash"] = row_hash(r["day"], signed, r["description"], seq)
    return rows


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
    if (info.get("format") == "card_invoice"
            or mapping.get("format") == "card_invoice"):
        return extract_card(path, mapping, password)
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
            })
        except (ValueError, KeyError) as exc:
            rejected.append({"line": i, "reason": str(exc),
                             "raw": {k: v for k, v in list(raw.items())[:4]}})
    return stamp_hashes(rows), rejected


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
            day = parse_date(hit["date"], date_format, hit.get("year"))
            description = hit["description"]
            rows.append({
                "day": day, "amount_cents": cents, "kind": kind,
                "description": description,
                "category": categorize(description),
            })
        except ValueError as exc:
            rejected.append({"line": hit["line"], "reason": str(exc),
                             "raw": hit["description"][:40]})
    return stamp_hashes(rows), rejected


def extract_card(path: Path, mapping: dict,
                 password: str | None = None) -> tuple[list[dict], list[dict]]:
    """A credit-card invoice's purchases. Everything on one is an expense.

    Except the rows that are not spending at all. The invoice carries its own
    settlement of the previous month -- "Inclusao de Pagamento 2.675,69", no
    sign, in the middle of the purchases. Imported as a purchase it nearly
    DOUBLES the month: on this owner's June invoice it would have turned
    R$ 2.975,48 of spending into R$ 5.651,17, and the figure would have looked
    ordinary. It is also already recorded on the bank statement, as the
    payment that leaves the account.
    """
    text, _ = read_text(path, password)
    closing = card_closing_date(text, mapping.get("closing"))
    if not closing:
        raise SystemExit(
            "this invoice does not print the year of its rows and no closing "
            "date was found in it. Ask the owner which month it closed and "
            "pass {\"format\":\"card_invoice\",\"closing\":\"YYYY-MM-DD\"} "
            "-- a guessed year files the whole invoice into a month nobody "
            "looks at")

    rows, rejected = [], []
    for hit in parse_card_lines(text, closing):
        if hit["credit"]:
            rejected.append({"line": hit["line"],
                             "reason": f"{hit['credit']}: not spending",
                             "raw": hit["description"][:40]})
            continue
        try:
            cents = m.parse_amount(hit["amounts"][0])
        except ValueError as exc:
            rejected.append({"line": hit["line"], "reason": str(exc),
                             "raw": hit["description"][:40]})
            continue
        if cents == 0:
            continue
        rows.append({
            "day": hit["date"], "amount_cents": abs(cents), "kind": "expense",
            "description": hit["description"],
            "category": categorize(hit["description"]),
        })
    return stamp_hashes(rows), rejected


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

    # What the owner's agent has already named beats the generic keyword rules
    # -- those are a guess about everyone, this is a decision about this
    # person's own statement. It is also why the second import asks nothing.
    learned = m.learned_categories(con)
    if learned:
        for r in rows:
            hit = m.categorize_learned(r["description"], learned)
            if hit:
                r["category"] = hit

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
        # Group by WHO, not by how much. Bucketing on the amount looked
        # tidy and quietly lost every salary that is not identical to the
        # cent -- which is most of them, once overtime or a holiday lands.
        # The amounts are checked for consistency further down instead.
        key = (r["kind"], strip_accents(label).lower()[:22])
        groups.setdefault(key, []).append((r["day_local"], r["amount_cents"], label))

    out, variable = [], []
    for (kind, _prefix), hits in groups.items():
        if len(hits) < min_occurrences:
            continue
        amounts = [h[1] for h in hits]
        typical = sorted(amounts)[len(amounts) // 2]
        # A recurring line is one whose amount is roughly stable. 25% is wide
        # enough for a salary with overtime or a bill that tracks usage, and
        # narrow enough that a supermarket does not look like rent.
        if typical and max(abs(a - typical) for a in amounts) > typical * 0.25:
            # Not a fixed line -- but a counterparty who pays repeatedly and
            # irregularly is not noise either, it is how a freelancer, a
            # contractor or anyone paid per job actually earns. Treating an
            # unstable payer as "no income" is what makes the projection
            # report that they cannot afford a coffee.
            if kind == "income":
                # Per MONTH, not per payment. One payer often sends the
                # wage and a handful of small settlements, so the smallest
                # payment might be R$64 and the largest R$8.800 -- a range
                # that says nothing true about what this person earns. What
                # they can spend is what arrived that month, however many
                # transfers it took.
                by_month: dict[tuple, int] = {}
                for day, cents, _lab in hits:
                    d = datetime.strptime(day, "%Y-%m-%d")
                    by_month[(d.year, d.month)] = (
                        by_month.get((d.year, d.month), 0) + cents)
                monthly = sorted(by_month.values())
                months = len(monthly) or 1
                variable.append({
                    "label": hits[-1][2][:40] or "income",
                    "amounts": amounts,
                    "monthly_totals": [by_month[k] for k in sorted(by_month)],
                    "occurrences": len(hits),
                    "months_seen": months,
                    "total_cents": sum(amounts),
                    "monthly_average_cents": sum(amounts) // months,
                    "monthly_low_cents": monthly[0],
                    "monthly_high_cents": monthly[-1],
                    "typical_cents": typical,
                    "varies": True,
                })
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

    variable.sort(key=lambda c: -c["total_cents"])

    # The dominant payer IS the salary, whatever its amount does.
    #
    # A wage with a component priced in another currency moves with the rate
    # every month; so does commission, and so does anything invoiced per job.
    # Reading that variance as "no salary" describes a large share of working
    # people as having no income at all, and the projection then tells them
    # they cannot afford anything. What varies is the amount, not the fact.
    #
    # So the biggest recurring payer's own average becomes the proposal, and
    # the spread is reported beside it rather than used to disqualify them.
    # Money you move between your own accounts is not money you earned.
    # It shows up as a payer with your own name on it, repeatedly, and it
    # inflated this owner's income by ~R$700/month until it was excluded.
    owner = strip_accents(m.get_cfg(con, "owner_name") or "").lower()
    if owner:
        parts = [w for w in owner.split() if len(w) > 2]
        def is_self(label: str) -> bool:
            flat = strip_accents(label).lower()
            hits = sum(1 for w in parts if w in flat)
            return hits >= max(2, len(parts) // 2)
        # Both lists. A standing order between your own accounts is
        # perfectly stable, so it lands among the REGULAR candidates and
        # would otherwise be proposed as a salary -- the tidiest possible
        # version of the same mistake.
        for c in variable + income:
            c["is_self_transfer"] = is_self(c["label"])
        variable = [c for c in variable if not c["is_self_transfer"]]
        income = [c for c in income if not c["is_self_transfer"]]

    primary = None
    if not income and variable:
        top = variable[0]
        rest = sum(c["monthly_average_cents"] for c in variable[1:])
        primary = {
            **{k: v for k, v in top.items() if k != "amounts"},
            "is_primary_payer": True,
            "other_payers_monthly_cents": rest,
            "confirm_with_owner": True,
        }

    total_variable = sum(c["monthly_average_cents"] for c in variable)
    suggested = (primary["monthly_average_cents"] + primary["other_payers_monthly_cents"]
                 if primary else sum(
                     monthly_equivalent_income(c) for c in income))

    return {
        "income_candidates": income,
        "fixed_expense_candidates": expense,
        "variable_income": [{k: v for k, v in c.items() if k != "amounts"}
                            for c in variable],
        "primary_payer": primary,
        "variable_income_monthly_average_cents": total_variable,
        "suggested_expected_income_cents": suggested,
        "has_regular_salary": bool(income),
        "next": (
            "confirm each with the owner before writing it -- a misread "
            "salary inverts every projection."
            + ("" if income or not variable else
               " This person's pay VARIES -- it is not missing. The biggest "
               "recurring payer is in primary_payer. Show the owner what "
               "that payer actually sent EACH MONTH (monthly_totals) and "
               "the average, and ASK THEM TO CONFIRM OR CORRECT IT before "
               "writing anything -- these are their earnings and only they "
               "know whether an odd month was a one-off. Never say 'no "
               "income found'. Once confirmed: `money.py config "
               "expected_income <cents>`.")),
    }


def monthly_equivalent_income(candidate: dict) -> int:
    return m.monthly_equivalent(candidate["amount_cents"],
                                candidate.get("frequency", "monthly"))


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
