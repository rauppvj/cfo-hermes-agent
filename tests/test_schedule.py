"""The cron registrar: what it registers, and what it must never do twice.

The expensive failure here is not a missing row, it is a DUPLICATE one. Two
cfo-brief rows is a brief twice an hour, forever, which is how an agent gets
muted -- and the only thing standing between this and that is how it decides
what is already registered. So these tests pin exactly that: an unreadable
schedule registers nothing, a readable one with the rows in it registers
nothing, and a missing home chat waits instead of creating a row that would
deliver nowhere.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "image" / "scripts" / "cfo-schedule.py"


def load(monkeypatch, tmp_path, *, home=None, channel="cht_abc"):
    """Import the script fresh, pointed at a temp home and a fake hermes."""
    home = home or tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "image-scripts"
    source.mkdir(exist_ok=True)
    for name in ("brief_gate.py", "notify_gate.py", "money.py", "bankalert.py"):
        (source / name).write_text(f"# {name} as the image ships it\n")
    # A fake `hermes` that records every argv it is called with, so a test can
    # assert on the rows that WOULD be created without a gateway anywhere.
    calls = tmp_path / "calls.jsonl"
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(calls)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
    fake.chmod(0o755)

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CFO_SCRIPT_SOURCE", str(source))
    monkeypatch.setenv("CFO_HERMES_BIN", str(fake))
    monkeypatch.setenv("PLOW_HOME_CHANNEL", channel)
    spec = importlib.util.spec_from_file_location("cfo_schedule", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cfo_schedule"] = module
    spec.loader.exec_module(module)
    # /run/s6/container_environment does not exist off a container, so the
    # fallback to the process environment is what these tests exercise.
    module._calls = lambda: [json.loads(l) for l in calls.read_text().splitlines()] \
        if calls.exists() else []
    return module, home


def test_a_fresh_home_gets_both_rows_and_the_gates(monkeypatch, tmp_path):
    module, home = load(monkeypatch, tmp_path)
    assert module.main([]) == 0
    staged = sorted(p.name for p in (home / "scripts").iterdir())
    assert staged == ["bankalert.py", "brief_gate.py", "money.py", "notify_gate.py"]
    rows = module._calls()
    names = [r[r.index("--name") + 1] for r in rows]
    assert names == ["cfo-brief", "cfo-notify"]
    brief = rows[0]
    assert brief[:3] == ["cron", "create", "0 * * * *"]
    assert brief[brief.index("--script") + 1] == "brief_gate.py"
    assert brief[brief.index("--skill") + 1] == "cfo-brief"
    assert brief[brief.index("--deliver") + 1] == "plow_chat:cht_abc"
    notify = rows[1]
    assert notify[2] == "*/5 * * * *"
    assert notify[notify.index("--script") + 1] == "notify_gate.py"


def test_rows_that_exist_are_left_alone(monkeypatch, tmp_path):
    module, home = load(monkeypatch, tmp_path)
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"name": "cfo-brief"}, {"name": "cfo-notify"}]}))
    assert module.main([]) == 0
    assert module._calls() == [], "a second copy of a row is a brief twice an hour"


def test_a_missing_row_beside_an_existing_one_is_the_only_one_created(monkeypatch, tmp_path):
    module, home = load(monkeypatch, tmp_path)
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"name": "cfo-brief"}]}))
    assert module.main([]) == 0
    rows = module._calls()
    assert [r[r.index("--name") + 1] for r in rows] == ["cfo-notify"]


def test_an_unreadable_schedule_registers_nothing(monkeypatch, tmp_path, capsys):
    module, home = load(monkeypatch, tmp_path)
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text("{not json")
    assert module.main([]) == 1
    assert module._calls() == [], \
        "registering over a schedule we cannot read is how one row becomes four"
    assert "cannot read" in capsys.readouterr().err


def test_a_schedule_that_is_not_a_list_of_jobs_registers_nothing(monkeypatch, tmp_path):
    module, home = load(monkeypatch, tmp_path)
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": "wat"}))
    assert module.main([]) == 1
    assert module._calls() == []


def test_no_home_chat_waits_rather_than_delivering_nowhere(monkeypatch, tmp_path, capsys):
    module, home = load(monkeypatch, tmp_path, channel="")
    assert module.main([]) == 1
    assert module._calls() == []
    assert "PLOW_HOME_CHANNEL" in capsys.readouterr().err
    # The gates are staged anyway: they are needed the moment a row exists,
    # and staging is what a rebuild has to land.
    assert (home / "scripts" / "brief_gate.py").is_file()


def test_stage_only_never_touches_the_schedule(monkeypatch, tmp_path):
    module, home = load(monkeypatch, tmp_path)
    assert module.main(["--stage-only"]) == 0
    assert module._calls() == []
    assert (home / "scripts" / "money.py").is_file()


def test_register_only_never_stages(monkeypatch, tmp_path):
    module, home = load(monkeypatch, tmp_path)
    assert module.main(["--register-only"]) == 0
    assert not (home / "scripts").exists()
    assert len(module._calls()) == 2


def test_staging_replaces_a_symlink_the_agent_left_in_place(monkeypatch, tmp_path):
    """Root writes here, and the directory belongs to the agent.

    A symlink at one of these names would make root's copy follow it out of
    the home -- and hermes refuses to run a script that resolves outside the
    scripts directory, so the row would fail every tick with the file looking
    perfectly present.
    """
    module, home = load(monkeypatch, tmp_path)
    (home / "scripts").mkdir()
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text("# not the gate\n")
    (home / "scripts" / "brief_gate.py").symlink_to(elsewhere)
    assert module.main(["--stage-only"]) == 0
    target = home / "scripts" / "brief_gate.py"
    assert not target.is_symlink()
    assert "as the image ships it" in target.read_text()
    assert elsewhere.read_text() == "# not the gate\n", "wrote through the link"


def test_an_unknown_argument_is_a_typo_not_a_pass(monkeypatch, tmp_path):
    module, _ = load(monkeypatch, tmp_path)
    assert module.main(["--stage"]) == 2
    assert module._calls() == []
