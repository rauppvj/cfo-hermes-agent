#!/usr/bin/env python3
"""Read a bank's own alert -- an SMS, a push notification -- as a transaction.

"Compra aprovada: R$ 45,90 em PADARIA CENTRAL, no débito" is not prose. It
is a form the bank filled in, and the three things the ledger needs are
always in it: the amount, who was paid, and -- in Brazil, where one card is
both -- whether it was DÉBITO (left the account now) or CRÉDITO (on the
invoice). A model can read these too, and did, until an empty one arrived
and it read a figure out of its memory instead. So the reading is code: it
either finds an amount or it does not, and it never invents one.

Runs on two very different Pythons -- the container's 3.13 and the Mac's
/usr/bin/python3, which may be 3.9 -- so nothing here needs anything newer
than 3.9 and nothing here imports the rest of the engine.

    >>> parse("Compra no débito aprovada: R$ 45,90 em PADARIA CENTRAL")["method"]
    'debit'
"""

from __future__ import annotations

import re
import unicodedata

AMOUNT = re.compile(
    r"(?:R\$|US\$|U\$|\$|€|£|BRL|USD)\s?"
    r"(\d{1,3}(?:\.\d{3})+,\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+[.,]\d{2}|\d+)")

# What kind of event the bank is describing. First match wins, so the
# specific ("negada") is checked before the general ("compra").
EVENTS = [
    ("declined",       r"\b(negad[ao]|recusad[ao]|nao aprovad[ao]|not approved|declined)\b"),
    ("invoice_closed", r"\b(fatura (fechou|fechada|disponivel|chegou)|invoice (is )?(ready|closed))\b"),
    ("refund",         r"\b(estorno|estornad[ao]|reembolso|refund(ed)?|devolucao)\b"),
    ("pix_in",         r"\b(voce recebeu|recebeu um pix|pix recebido|received a pix|recebido)\b"),
    ("pix_out",        r"\b(pix (enviado|realizado|efetuado|feito)|voce enviou|enviou um pix|transferencia (enviada|realizada|efetuada))\b"),
    ("purchase",       r"\b(compra|purchase|charged|transaction|pagamento aprovado|debito autorizado|autorizad[ao])\b"),
]

METHOD_DEBIT = re.compile(r"\b(debito|debit|conta corrente|pix)\b")
METHOD_CREDIT = re.compile(r"\b(credito|credit|fatura|parcelad[ao]|\d+x)\b")

# Who was paid. Real alerts put the payee right after the amount -- "R$ 45,90
# em PADARIA", "R$ 1.234,56, LOJA DO POSTO, 09/09", "Pix de R$ 300,00 para
# Bruna", "charged $12.40 at CORNER BAKERY" -- so the amount is the anchor
# and the name is what follows it, up to the next clause. The words that end
# a name are the ones alerts continue with: "no crédito", "foi aprovada",
# "cartão final", a date.
STOP = (r"(?:foi|was|is|esta|no|na|com|para|as|às|em|dia|final|cartao|cart[aã]o|"
        r"aprovad\w*|approved|autorizad\w*|on|using|with|creditad\w*|debitad\w*|"
        r"parcelad\w*|\d{1,2}/\d{1,2})")
# A name runs to the next clause. A dot ends a clause -- "Padaria. Criciúma,
# SC" -- except when a digit follows it: delivery apps and acquirers put an
# order or terminal number in the name, "IFD*56.184.581 RESTAURANTE", and
# cutting at the first dot left "IFD*56" in the ledger.
NAME = r"(?P<m>(?:[^,.;\n]|\.(?=\d))+?)"
END = r"(?=\s+" + STOP + r"\b|\s*(?:[,;\n]|\.(?!\d))|$)"
AFTER_AMOUNT = re.compile(
    r"^\s*(?:,\s*|[-–—]\s*|(?:de|em|at|para|to|from)\s+)" + NAME + END, re.I)
ANYWHERE = re.compile(
    r"\b(?:em|at|no estabelecimento|na loja|para|to)\s+" + NAME + END, re.I)
NOISE_TAIL = re.compile(r"\s*(?:\*+\d{4}|final \d{4})\s*$", re.I)
DATE_LIKE = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:\s+\d{1,2}:\d{2})?$")


STOP_WORD = re.compile(STOP + r"$", re.I)
HAS_DATE = re.compile(r"\d{1,2}/\d{1,2}")


def _clean(candidate: str) -> str | None:
    name = NOISE_TAIL.sub("", candidate.strip(" -–—*")).strip()
    if not name or AMOUNT.search(name) or HAS_DATE.search(name):
        return None
    if STOP_WORD.match(name.split()[0]):
        # "dia 09/09/2026 às 15:17" sits between the amount and the "em
        # LOJA" in a C6 alert; a name never starts with one of these words.
        return None
    return name


def _merchant(raw: str, amount_match, body: str | None = None,
              wallet: bool = False) -> str | None:
    candidates = []
    if amount_match:
        hit = AFTER_AMOUNT.match(raw[amount_match.end():])
        if hit:
            candidates.append(hit.group("m"))
    hit = ANYWHERE.search(raw)
    if hit:
        candidates.append(hit.group("m"))
    for c in candidates:
        name = _clean(c)
        if name:
            return name
    # Apple Wallet's own notification for a tap is "<Merchant>. <City>, <ST>"
    # then the amount: the merchant is the first clause of the body, and
    # nothing else in it says "compra". Only when nothing better was found,
    # and only for a short clause -- a bank's sentence is never five words.
    if wallet and body:
        first = re.split(r"\.(?!\d)|\n", body.strip(), maxsplit=1)[0].strip()
        if first and len(first.split()) <= 6 and not AMOUNT.search(first):
            return first
    return None


def is_wallet(app: str) -> bool:
    a = (app or "").lower()
    return "passbook" in a or "passkit" in a or "wallet" in a


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").lower()


def parse_amount(raw: str) -> int:
    """'1.234,56' | '1,234.56' | '45,90' | '12.40' | '45' -> cents."""
    s = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d{2}", s):
        s = s.replace(",", "")
    elif re.fullmatch(r"\d+,\d{2}", s):
        s = s.replace(",", ".")
    return int(round(float(s) * 100))


def parse(text: str, app: str = "", body: str | None = None) -> dict:
    """What the alert says, as fields. `ok` is False when it should not be
    logged -- no amount, a declined purchase, an invoice closing -- and
    `event` says why. `app` is the notifying app's identifier when known:
    Apple Wallet's own notification names no event, and is a purchase."""
    raw = (text or "").strip()
    wallet = is_wallet(app)
    flat = _fold(raw)
    out = {"ok": False, "event": "unknown", "amount_cents": None,
           "merchant": None, "method": None, "kind": None, "text": raw}

    m = AMOUNT.search(raw)
    if m:
        try:
            out["amount_cents"] = parse_amount(m.group(1))
        except ValueError:
            out["amount_cents"] = None

    for event, pattern in EVENTS:
        if re.search(pattern, flat):
            out["event"] = event
            break
    if out["event"] == "unknown" and wallet and out["amount_cents"]:
        out["event"] = "purchase"

    if METHOD_DEBIT.search(flat):
        out["method"] = "debit"
    elif METHOD_CREDIT.search(flat):
        out["method"] = "credit"

    out["merchant"] = _merchant(raw, m, body=body, wallet=wallet)

    if out["event"] in ("purchase", "pix_out"):
        out["kind"] = "expense"
        if out["event"] == "pix_out":
            out["method"] = "debit"
    elif out["event"] in ("pix_in", "refund"):
        out["kind"] = "income"
        out["method"] = "debit"

    out["ok"] = bool(out["amount_cents"] and out["kind"])
    return out


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(parse(" ".join(sys.argv[1:]) or sys.stdin.read()),
                     ensure_ascii=False, indent=2))
