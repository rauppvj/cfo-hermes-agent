"""Tests for the statement importer.

The design claim under test is that this works for a bank nobody wrote a
parser for. So the fixtures are two statements that share almost nothing: a
Brazilian one (semicolon, DD/MM/YYYY, comma decimals, a title block above the
header, one signed amount column) and a US one (comma, MM/DD/YYYY, dot
decimals, separate Debit/Credit columns and no minus sign anywhere).

If both import correctly from a column mapping alone, the claim holds.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cfo-shared" / "scripts"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

BR = FIXTURES / "extrato_br.csv"
US = FIXTURES / "statement_us.csv"

BR_MAP = {"date": "Data", "amount": "Valor", "description": "Historico",
          "sign": "negative_is_expense", "date_format": "%d/%m/%Y"}
US_MAP = {"date": "Date", "description": "Description", "debit": "Debit",
          "credit": "Credit", "date_format": "%m/%d/%Y"}


@pytest.fixture()
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("CFO_DATA", str(tmp_path))
    import importlib
    import money
    importlib.reload(money)
    import statement
    importlib.reload(statement)
    return statement


@pytest.fixture()
def con(st):
    import money as m
    c = m.connect()
    m.set_cfg(c, "timezone", "America/Sao_Paulo")
    yield c
    c.close()


# -- reading a shape nobody wrote a parser for ------------------------------

def test_finds_the_header_under_a_title_block(st):
    """The Brazilian fixture opens with two lines of branch/account preamble.
    A naive reader takes line 1 as the header and imports nothing."""
    info = st.sniff(BR)
    assert info["delimiter"] == ";"
    assert info["headers"] == ["Data", "Historico", "Valor", "Saldo"]
    assert info["total_rows"] > 100


def test_inspect_shows_a_sample_not_the_file(st):
    """The whole design rests on the model never seeing every row."""
    info = st.sniff(BR)
    assert len(info["sample"]) <= st.SAMPLE_ROWS
    assert info["total_rows"] > len(info["sample"]) * 10


def test_both_statements_import_from_a_mapping_alone(st, con):
    br, _ = st.extract(BR, BR_MAP)
    us, _ = st.extract(US, US_MAP)
    assert len(br) > 100 and len(us) > 100
    # signed single column, and a debit/credit pair, both resolve direction
    assert {r["kind"] for r in br} == {"expense", "income"}
    assert {r["kind"] for r in us} == {"expense", "income"}


def test_amounts_survive_both_locales(st):
    br, _ = st.extract(BR, BR_MAP)
    salary = [r for r in br if "SALARIO" in r["description"]][0]
    assert salary["amount_cents"] == 700000   # "7.000,00", not 7.00
    assert salary["kind"] == "income"

    us, _ = st.extract(US, US_MAP)
    pay = [r for r in us if "PAYROLL" in r["description"]][0]
    assert pay["amount_cents"] == 420000      # "4200.00"
    assert pay["kind"] == "income"


def test_dates_respect_the_declared_format(st):
    """03/04 is April in the BR file and March in the US one. Getting this
    wrong silently moves a third of the year."""
    br, _ = st.extract(BR, BR_MAP)
    assert all(r["day"].startswith(("2026-06", "2026-07", "2026-08")) for r in br)


# -- the three safety properties -------------------------------------------

def test_dry_run_writes_nothing(st, con):
    out = st.apply(con, BR, BR_MAP, dry_run=True)
    assert out["to_import"] > 100
    assert con.execute("SELECT COUNT(*) AS n FROM tx").fetchone()["n"] == 0


def test_importing_the_same_statement_twice_is_free(st, con):
    first = st.apply(con, BR, BR_MAP, dry_run=False)
    n_after_first = con.execute("SELECT COUNT(*) AS n FROM tx").fetchone()["n"]

    second = st.apply(con, BR, BR_MAP, dry_run=False)
    n_after_second = con.execute("SELECT COUNT(*) AS n FROM tx").fetchone()["n"]

    assert first["imported"] > 100
    assert second["to_import"] == 0
    assert second["already_present"] == first["imported"]
    assert n_after_second == n_after_first


def test_an_import_is_one_batch_and_undoes_whole(st, con):
    import money as m
    mine = m.add_tx(con, 4200, "expense", "food", note="typed by hand")
    out = st.apply(con, BR, BR_MAP, dry_run=False)

    st.undo(con, out["batch"])

    rows = con.execute("SELECT id FROM tx").fetchall()
    assert [r["id"] for r in rows] == [mine], "undo took the owner's own rows"


def test_batches_are_listed_for_the_owner(st, con):
    out = st.apply(con, BR, BR_MAP, dry_run=False)
    listed = st.batches(con)
    assert listed[0]["batch"] == out["batch"]
    assert listed[0]["transactions"] == out["imported"]


# -- the auto-configuration ------------------------------------------------

def test_detect_finds_the_salary_and_its_frequency(st, con):
    st.apply(con, BR, BR_MAP, dry_run=False)
    found = st.detect_recurring(con)

    salary = found["income_candidates"][0]
    assert salary["amount_cents"] == 700000
    assert salary["frequency"] == "monthly"
    assert salary["day"] == 5
    assert salary["occurrences"] >= 3


def test_detect_finds_fixed_bills_across_both_countries(st, con):
    st.apply(con, BR, BR_MAP, dry_run=False)
    br = {c["label"][:7] for c in st.detect_recurring(con)["fixed_expense_candidates"]}
    assert any(l.startswith("ALUGUEL") for l in br)

    con.execute("DELETE FROM tx")
    con.commit()
    st.apply(con, US, US_MAP, dry_run=False)
    us = st.detect_recurring(con)
    assert us["income_candidates"][0]["amount_cents"] == 420000
    assert any("RENT" in c["label"] for c in us["fixed_expense_candidates"])


def test_detect_proposes_but_never_writes(st, con):
    st.apply(con, BR, BR_MAP, dry_run=False)
    st.detect_recurring(con)
    assert con.execute("SELECT COUNT(*) AS n FROM fixed").fetchone()["n"] == 0


# -- categorisation --------------------------------------------------------

@pytest.mark.parametrize("description,category", [
    ("IFOOD *RESTAURANTE", "food"),
    ("SUPERMERCADO ANGELONI", "groceries"),
    ("UBER *TRIP", "transport"),
    ("NETFLIX.COM", "subscriptions"),
    ("DROGARIA SAO PAULO", "health"),
    ("WHOLE FOODS MKT", "groceries"),
    ("STARBUCKS #4412", "food"),
    ("CVS PHARMACY", "health"),
    ("ALUGUEL IMOBILIARIA", "housing"),
    ("PAGAMENTO XYZ QUALQUER", "other"),
])
def test_categories_work_in_both_languages(st, description, category):
    assert st.categorize(description) == category


def test_accents_do_not_break_matching(st):
    assert st.categorize("FARMÁCIA SÃO JOÃO") == "health"
    assert st.categorize("CAFÉ DA ESQUINA") == "food"


# -- statements that arrived as a PDF --------------------------------------

PDF = FIXTURES / "extrato_pdf.txt"
PDF_MAP = {"format": "text", "sign": "negative_is_expense",
           "date_format": "%d/%m/%Y"}


def test_a_layout_is_not_mistaken_for_a_csv(st):
    """The trap this cost an evening: '-1.800,00   3.200,00' holds two commas
    on every line of a statement, so both 'count the commas' and 'count them
    consistently' call a de-PDF'd layout a CSV, and it imports as nonsense."""
    assert st.looks_like_csv(BR.read_text(encoding="latin-1")) is True
    assert st.looks_like_csv(US.read_text()) is True
    assert st.looks_like_csv(PDF.read_text()) is False


def test_all_three_formats_are_detected_without_being_told(st):
    assert st.sniff(BR).get("format") is None      # csv
    assert st.sniff(US).get("format") is None      # csv
    assert st.sniff(PDF)["format"] == "text"


def test_pdf_text_import_reads_amounts_and_direction(st, con):
    out = st.apply(con, PDF, PDF_MAP, dry_run=False)
    assert out["imported"] > 100
    assert out["unreadable"] == 0
    assert out["expense"] > 0 and out["income"] > 0


@pytest.mark.parametrize("raw,expected", [
    ("-1.800,00", "-1.800,00"),
    ("1.800,00-", "-1.800,00"),      # trailing minus
    ("-    141,37", "-141,37"),      # sign left at the column edge
    ("141,37 D", "-141,37"),         # debit marker instead of a sign
    ("141,37 C", "141,37"),          # credit marker
    ("7.000,00", "7.000,00"),
])
def test_every_way_a_bank_writes_a_minus(st, raw, expected):
    """A PDF right-aligns the digits and leaves the sign at the column edge,
    and plenty of banks use a D/C marker instead of a sign at all. Reading
    any of these as positive turns an expense into income."""
    assert st.normalise_sign(raw).replace(" ", "") == expected


def test_the_balance_column_is_not_imported_as_a_transaction(st, con):
    """Each line carries the amount AND the running balance. Taking the
    second number would import the balance as spending."""
    rows, _ = st.extract(PDF, PDF_MAP)
    salary = [r for r in rows if "SALARIO" in r["description"]][0]
    assert salary["amount_cents"] == 700000     # not the balance beside it


def test_page_furniture_is_skipped(st, con):
    """The fixture repeats a bank header and 'Pagina 2' every 40 days."""
    rows, rejected = st.extract(PDF, PDF_MAP)
    assert not any("Ouvidoria" in r["description"] for r in rows)
    assert not any("Pagina" in r["description"] for r in rows)
    assert rejected == []


def test_detect_works_the_same_on_a_pdf_derived_statement(st, con):
    st.apply(con, PDF, PDF_MAP, dry_run=False)
    found = st.detect_recurring(con)
    assert found["income_candidates"][0]["amount_cents"] == 700000
    assert found["income_candidates"][0]["frequency"] == "monthly"


# -- errors never reach a phone --------------------------------------------

def test_a_missing_file_is_json_not_a_traceback(st, capsys):
    rc = st._run(st.main, ["inspect", "/nope/missing.csv"])
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False and "error" in payload


def test_an_unknown_subcommand_is_json_not_a_usage_string(st, capsys):
    """This exact case was delivered to someone's phone as the answer."""
    rc = st._run(st.main, ["inspekt", "x"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload["ok"] is False
    assert "invalid choice" in payload["error"]
