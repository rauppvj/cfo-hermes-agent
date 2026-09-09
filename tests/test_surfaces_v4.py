"""The surfaces that read the v4 ledger: panel, gate, doctor.

Each of these is code that speaks for the ledger with nobody reading over
its shoulder -- a wall at 3am, a cron tick, a host verifying an install. What
they say has to be checkable as data before it is markup.
"""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from test_money import con, mod  # noqa: F401  (fixtures)

SCRIPTS = Path(__file__).resolve().parents[1] / "cfo-shared" / "scripts"
SP = ZoneInfo("America/Sao_Paulo")


def at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=SP)


@pytest.fixture()
def panel(con):
    import importlib
    import panel as p
    importlib.reload(p)
    return p


@pytest.fixture()
def gate(con):
    import importlib
    import brief_gate as g
    importlib.reload(g)
    return g


# -- the panel ---------------------------------------------------------------

def test_the_panel_shows_the_account_only_from_a_reading(mod, con, panel):
    mod.set_cfg(con, "language", "en")
    mod.add_tx(con, 4000, "expense", "food", when=at(2026, 9, 8))
    snap = panel.snapshot(con, today=at(2026, 9, 9))
    assert snap["balance"]["has_anchor"] is False
    assert snap["balance"]["balance_fmt"] is None
    html = panel.render(snap)
    assert "tell the agent what is in the account" in html

    mod.set_balance(con, 131240, when=at(2026, 9, 8, 22))
    mod.add_tx(con, 6000, "expense", "shopping", method="credit", when=at(2026, 9, 9, 9))
    snap = panel.snapshot(con, today=at(2026, 9, 9, 10))
    assert snap["balance"]["balance_fmt"] == "R$ 1.312,40"
    assert snap["balance"]["card_open_fmt"] == "R$ 60,00"
    html = panel.render(snap)
    assert "in the account" in html and "R$ 1.312,40" in html
    assert "on the card, unpaid" in html and "R$ 60,00" in html


def test_a_budgeted_category_is_drawn_against_its_ceiling(mod, con, panel):
    mod.set_cfg(con, "language", "en")
    mod.set_budget(con, "food", 10000)
    mod.add_tx(con, 11000, "expense", "food", when=at(2026, 9, 8))
    mod.add_tx(con, 30000, "expense", "housing", when=at(2026, 9, 8))
    snap = panel.snapshot(con, today=at(2026, 9, 9))
    rows = {c["category"]: c for c in snap["categories"]}
    assert rows["food"]["limit_fmt"] == "R$ 100,00"
    assert rows["food"]["pct_of_limit"] == 100          # clamped, not 110
    assert rows["food"]["over"] is True
    assert rows["housing"]["limit_fmt"] is None
    html = panel.render(snap)
    assert 'class="over"' in html and "of R$ 100,00" in html


def test_the_panel_speaks_the_owners_language_not_the_currencys(mod, con, panel):
    # BRL, but the owner writes English
    mod.set_cfg(con, "language", "en")
    assert panel.language(con) == "en"
    mod.set_cfg(con, "language", "xx")     # no dictionary: English, not a crash
    assert panel.language(con) == "en"
    con.execute("DELETE FROM config WHERE key = 'language'")
    con.commit()
    assert panel.language(con) == "pt"     # from the currency


# -- the gate ----------------------------------------------------------------

def test_the_gate_names_the_language_and_the_monday(mod, con, gate):
    mod.set_cfg(con, "language", "en")
    monday = at(2026, 9, 14, 8)
    text = gate.announce("morning", 8, monday, "America/Sao_Paulo", con)
    assert "Write it in English" in text
    assert "Monday" in text and "money.py week" in text
    tuesday = at(2026, 9, 15, 8)
    text = gate.announce("morning", 8, tuesday, "America/Sao_Paulo", con)
    assert "Monday" not in text
    # evening never carries the week line
    text = gate.announce("evening", 22, monday, "America/Sao_Paulo", con)
    assert "money.py week" not in text
    # no setting: the currency decides, and BRL is Portuguese
    con.execute("DELETE FROM config WHERE key = 'language'")
    con.commit()
    assert "Write it in Portuguese" in gate.announce("morning", 8, tuesday, "x", con)


# -- the doctor --------------------------------------------------------------

def _fake_home(tmp_path, mod, con) -> Path:
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    (home / "config.yaml").write_text("model: x\n")
    (home / "SOUL.md").write_text("# rules\nYou do not do arithmetic. Ever.\n")
    for skill in ("cfo-log", "cfo-ask", "cfo-simulate", "cfo-brief",
                  "cfo-setup", "cfo-import", "cfo-panel"):
        (home / "skills" / skill).mkdir()
        (home / "skills" / skill / "SKILL.md").write_text("---\nname: x\n---\n")
    shutil.copytree(SCRIPTS, home / "skills" / "cfo-shared" / "scripts")
    (home / "scripts").mkdir()
    for name in ("brief_gate.py", "notify_gate.py", "panel.py", "usage_report.sh"):
        shutil.copy(SCRIPTS / name, home / "scripts" / name)
    (home / "scripts" / "agent_index_client.py").write_text(
        "import sys\nprint('  registered: install abc')\nsys.exit(0)\n")
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"name": n, "enabled": True, "last_run": "2026-09-09T11:00:00-03:00"}
        for n in ("cfo-brief", "cfo-panel", "cfo-usage", "cfo-notify")]}))
    (home / "logs").mkdir()
    (home / "logs" / "agent-index.log").write_text(
        "2026-09-09T14:17:52Z agent=cfo days=4\n"
        "  200 {'ok': True, 'agent_id': 'cfo'}\n")
    return home


def test_the_doctor_passes_a_complete_install_and_names_a_stale_script(mod, con, tmp_path):
    import importlib
    import doctor
    importlib.reload(doctor)
    home = _fake_home(tmp_path, mod, con)
    import panel
    panel.write(con)                       # so the panel check has a file
    checks = {c["check"]: c for c in doctor.run(home)}
    failing = [n for n, c in checks.items() if not c["ok"]]
    assert failing == [], failing
    assert checks["skills"]["detail"].startswith("8/8")
    assert checks["index registration"]["ok"]
    assert checks["setup"]["ok"]           # informational on a fresh ledger

    (home / "scripts" / "brief_gate.py").write_text("# an older deploy\n")
    checks = {c["check"]: c for c in doctor.run(home)}
    assert checks["scripts/brief_gate.py"]["ok"] is False
    assert "STALE" in checks["scripts/brief_gate.py"]["detail"]

    (home / "SOUL.md").write_text("# the image's own\n")
    checks = {c["check"]: c for c in doctor.run(home)}
    assert checks["soul"]["ok"] is False


def test_the_doctor_reports_an_empty_home_as_not_this_agent(mod, con, tmp_path):
    import importlib
    import doctor
    importlib.reload(doctor)
    checks = {c["check"]: c for c in doctor.run(tmp_path / "nowhere")}
    assert checks["home"]["ok"] is False
    assert checks["skills"]["ok"] is False
    assert checks["soul"]["ok"] is False
