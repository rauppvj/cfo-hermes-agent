"""The bank's alert, read as fields -- for the shapes Brazilian banks and a
couple of others actually send. Each case pins the amount in cents, the
merchant, and the one thing a Wallet tap cannot know: débito or crédito."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"))

import bankalert  # noqa: E402


@pytest.mark.parametrize("text,cents,merchant,method,kind", [
    # a purchase-card push, credit function
    ("Compra aprovada: R$ 45,90 em PADARIA CENTRAL no crédito, cartão final 1234.",
     4590, "PADARIA CENTRAL", "credit", "expense"),
    # the same card, debit function
    ("Compra no débito aprovada: R$ 45,90 em PADARIA CENTRAL",
     4590, "PADARIA CENTRAL", "debit", "expense"),
    # an SMS with the amount before the merchant and a date after
    ("ITAU: compra aprovada cartao final 1234, R$ 1.234,56, LOJA DO POSTO, 09/09 14:32",
     123456, "LOJA DO POSTO", None, "expense"),
    # nothing about the function: a card purchase is credit until told otherwise
    ("Sua compra de R$ 89,90 em LOJA ONLINE foi aprovada.",
     8990, "LOJA ONLINE", None, "expense"),
    # a Pix out is the account, to a person
    ("Pix enviado: R$ 300,00 para Bruna Silva",
     30000, "Bruna Silva", "debit", "expense"),
    # a Pix in is income
    ("Você recebeu um Pix de R$ 260,15 de FULANO DE TAL",
     26015, "FULANO DE TAL", "debit", "income"),
    # english, dollar, no function
    ("Your card ending 1234 was charged $12.40 at CORNER BAKERY",
     1240, "CORNER BAKERY", None, "expense"),
    # an instalment purchase is credit by construction
    ("Compra aprovada: R$ 1.200,00 em LOJA DE MOVEIS, parcelado em 3x",
     120000, "LOJA DE MOVEIS", "credit", "expense"),
])
def test_alerts_read_as_fields(text, cents, merchant, method, kind):
    p = bankalert.parse(text)
    assert p["ok"] is True
    assert p["amount_cents"] == cents
    assert p["merchant"] == merchant
    assert p["method"] == method
    assert p["kind"] == kind


@pytest.mark.parametrize("text,event", [
    ("Compra negada: R$ 45,90 em LOJA X. Fale com o banco.", "declined"),
    ("Sua fatura fechou: R$ 2.841,17. Vence dia 10.", "invoice_closed"),
    ("Seu código de acesso é 123456", "unknown"),
    ("💳  ·  · ", "unknown"),
])
def test_what_is_not_a_purchase_is_not_logged(text, event):
    p = bankalert.parse(text)
    assert p["ok"] is False
    assert p["event"] == event


def test_an_empty_alert_never_yields_an_amount():
    p = bankalert.parse("")
    assert p["amount_cents"] is None and p["ok"] is False


def test_a_refund_is_income_back_to_the_account():
    p = bankalert.parse("Estorno de R$ 59,90 de LOJA X creditado")
    assert (p["kind"], p["method"], p["amount_cents"]) == ("income", "debit", 5990)


# -- the shapes one Brazilian bank actually sent, with the card and shop changed

def test_a_date_clause_between_the_amount_and_the_shop_is_not_the_shop():
    p = bankalert.parse("Compra no débito aprovada Sua compra no cartão final 1234 no valor "
                        "de R$ 57,96, dia 09/09/2026 às 15:17, em SUPERMERCADO BOM PRECO "
                        "SAO PAULO BRA, foi aprovada.")
    assert p["ok"] and p["amount_cents"] == 5796 and p["method"] == "debit"
    assert p["merchant"] == "SUPERMERCADO BOM PRECO SAO PAULO BRA"


def test_apple_wallets_own_notification_is_a_purchase_named_by_its_first_clause():
    body = "Supermercado Bom Preco. São Paulo, SP\nR$ 57,96"
    p = bankalert.parse("C6 Bank " + body, app="com.apple.Passbook", body=body)
    assert p["ok"] and p["event"] == "purchase" and p["amount_cents"] == 5796
    assert p["merchant"] == "Supermercado Bom Preco"
    assert p["method"] is None          # Wallet does not know débito from crédito
    # the same text from an unknown app names no event and is not logged
    assert bankalert.parse("C6 Bank " + body, body=body)["ok"] is False


# The night a delivery-app purchase arrived: the bank names the payee
# "IFD*<order number> <restaurant> <city> BRA", and the order number carries
# dots. The first version of the reader ended the name at the first dot and
# "IFD*56" went into the ledger -- unclassifiable, and not the shop.
def test_a_dotted_order_number_is_part_of_the_merchant_name():
    text = ("Compra no crédito aprovada Sua compra no cartão final 4321 no valor "
            "de R$ 48,20, dia 09/09/2026 às 21:04, em IFD*12.345.678 RESTAURANTE "
            "BOM PRATO CIDADE BRA, foi aprovada.")
    p = bankalert.parse(text)
    assert p["ok"] and p["amount_cents"] == 4820 and p["method"] == "credit"
    assert p["merchant"] == "IFD*12.345.678 RESTAURANTE BOM PRATO CIDADE BRA"


def test_wallets_notification_for_a_dotted_merchant_keeps_the_whole_name():
    body = "IFD* 12.345.678 Restaurante Bom Prato\nR$ 48,20"
    p = bankalert.parse("Banco X " + body, app="com.apple.Passbook", body=body)
    assert p["ok"] and p["event"] == "purchase" and p["amount_cents"] == 4820
    assert p["merchant"] == "IFD* 12.345.678 Restaurante Bom Prato"


def test_a_dot_before_a_word_still_ends_the_name():
    p = bankalert.parse("Compra aprovada: R$ 12,00 em PADARIA CENTRAL. Fale com o banco.")
    assert p["merchant"] == "PADARIA CENTRAL"
