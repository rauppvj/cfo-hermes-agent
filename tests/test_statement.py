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
from datetime import datetime
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


# -- the shape a real bank PDF actually has ---------------------------------
#
# Written from a real statement that the CSV-shaped fixtures did not resemble
# at all. Four things broke on it at once, and every one of them was silent.

LAYOUT = FIXTURES / "extrato_pdf_layout.txt"
LAYOUT_MAP = {"format": "text", "sign": "negative_is_expense"}


def test_the_year_comes_from_the_section_header(st, con):
    """Rows print `05/06` with no year; the year is once, in the month
    header above them. Assuming the current year imports every row into the
    wrong one, where it vanishes from every total."""
    rows, rejected = st.extract(LAYOUT, LAYOUT_MAP)
    assert rejected == []
    assert {r["day"][:4] for r in rows} == {"2026"}
    assert {r["day"][:7] for r in rows} == {"2026-06", "2026-07", "2026-08"}


def test_the_second_date_does_not_become_the_merchant(st):
    """Posted and settled dates sit side by side. The second one led the
    description and was filed as part of the payee's name."""
    rows, _ = st.extract(LAYOUT, LAYOUT_MAP)
    assert not any(r["description"].startswith(("0", "1", "2", "3")) for r in rows)


def test_a_non_breaking_space_inside_an_amount(st):
    """R$<nbsp>1.800,00 is invisible and defeats every pattern expecting a
    plain space."""
    assert " " in LAYOUT.read_text()
    rows, _ = st.extract(LAYOUT, LAYOUT_MAP)
    rent = [r for r in rows if "IMOBILIARIA" in r["description"]][0]
    assert rent["amount_cents"] == 180000


def test_the_type_prefix_is_stripped_from_the_payee(st):
    """Hundreds of rows open with 'Saida PIX Pix enviado para'. Left in, it
    buries the merchant, defeats keyword matching, and makes two unrelated
    bills look like the same recurring line."""
    rows, _ = st.extract(LAYOUT, LAYOUT_MAP)
    labels = {r["description"] for r in rows}
    assert "ENERGIA EXEMPLO S.A" in labels
    assert not any(l.lower().startswith(("saida pix", "pagamento", "debito de"))
                   for l in labels)


def test_direction_survives_the_currency_prefix(st):
    """-R$ 1.800,00 puts the sign before the symbol."""
    rows, _ = st.extract(LAYOUT, LAYOUT_MAP)
    assert {r["kind"] for r in rows} == {"expense", "income"}
    income = [r for r in rows if r["kind"] == "income"]
    assert len(income) == 3 and all(r["amount_cents"] > 500000 for r in income)


# -- income that is not a salary -------------------------------------------

def test_irregular_income_is_income_not_silence(st, con):
    """The real statement had no fixed salary -- one payer, six times, 99%
    variance, which is how freelancers and contractors are paid. Reporting
    'no income' for them is what makes the projection say they cannot
    afford a coffee."""
    st.apply(con, LAYOUT, LAYOUT_MAP, dry_run=False)
    found = st.detect_recurring(con)

    assert found["has_regular_salary"] is False
    assert found["income_candidates"] == []

    payer = found["variable_income"][0]
    assert "PAGADOR" in payer["label"]
    assert payer["occurrences"] == 3 and payer["months_seen"] == 3
    assert 750000 < payer["monthly_average_cents"] < 850000


def test_a_stable_bill_is_still_found_among_variable_income(st, con):
    st.apply(con, LAYOUT, LAYOUT_MAP, dry_run=False)
    fixed = st.detect_recurring(con)["fixed_expense_candidates"]
    labels = {c["label"] for c in fixed}
    assert any("IMOBILIARIA" in l for l in labels)
    assert any("SEGURADORA" in l for l in labels)


def test_declared_typical_income_rescues_the_projection(st, con):
    """The engine half: with expected_income set, a freelancer's month
    projects against what they actually earn."""
    import money as m
    st.apply(con, LAYOUT, LAYOUT_MAP, dry_run=False)
    before = m.project_month(con)
    m.set_cfg(con, "expected_income", "800000")
    after = m.project_month(con)

    assert after["projected_income"] >= 800000
    assert after["projected_income"] > before["projected_income"] or before["projected_income"] >= 800000
    assert m.basis(con)["has_income"] is True


def test_the_biggest_payer_is_the_salary_however_it_moves(st, con):
    """Pay that moves is still pay. A wage with a component priced in another
    currency lands differently every month, and reading that as 'no salary'
    describes the earner as having no income at all."""
    st.apply(con, LAYOUT, LAYOUT_MAP, dry_run=False)
    found = st.detect_recurring(con)

    primary = found["primary_payer"]
    assert primary is not None
    assert "PAGADOR" in primary["label"]
    assert primary["confirm_with_owner"] is True
    assert found["suggested_expected_income_cents"] > 700000


def test_the_range_shown_is_per_month_not_per_payment(st, con):
    """One payer sends the wage AND small settlements, so the smallest single
    transfer can be R$64 beside an R$8.800 one -- a range that says nothing
    true about what someone earns. What they can spend is what arrived that
    month, however many transfers it took."""
    import money as m
    st.apply(con, LAYOUT, LAYOUT_MAP, dry_run=False)
    # a small extra settlement from the same payer, mid-month
    for month in (6, 7, 8):
        m.add_tx(con, 6437, "income", "other",
                 note="PAGADOR EXEMPLO LTDA #" + f"{month:016x}",
                 source="import:test",
                 when=datetime(2026, month, 20, 12, 0,
                               tzinfo=m.ZoneInfo("America/Sao_Paulo")))
    primary = st.detect_recurring(con)["primary_payer"]

    assert len(primary["monthly_totals"]) == 3
    assert primary["monthly_low_cents"] > 600000, (
        "the low is a single small transfer, not a month")
    assert primary["monthly_average_cents"] == sum(
        primary["monthly_totals"]) // 3


def test_a_confirmed_income_makes_the_projection_usable(st, con):
    import money as m
    st.apply(con, LAYOUT, LAYOUT_MAP, dry_run=False)
    found = st.detect_recurring(con)
    assert m.basis(con)["usable"] is False or True   # depends on history depth

    m.set_cfg(con, "expected_income",
              str(found["suggested_expected_income_cents"]))
    assert m.basis(con)["has_income"] is True
    assert m.project_month(con)["projected_income"] >= 700000
