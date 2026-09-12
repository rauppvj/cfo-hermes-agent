"""The two context settings, merged into a file that belongs to other people.

The failure this prevents is the one that broke the owner's agent on
2026-09-10: an unbounded DM session, ~175,000 tokens of history and provider
reasoning, and a provider that answers with a first byte and then nothing for
ninety minutes. Two card taps arrived during it and neither was recorded.

What these tests pin is not the numbers -- those are a judgement call, and an
owner who tunes them keeps their value -- but the merge's manners. config.yaml
is seeded by the base image, asserted on every boot by plow-init, and
rewritten in place by the chat plugin. A writer that replaces the file, or
loses a key, or fails a boot over a parse error, is worse than one that never
ran.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
SOURCE = Path(__file__).resolve().parents[1] / "image" / "scripts" / "cfo-config.py"


def load(monkeypatch, path):
    monkeypatch.setenv("CFO_CONFIG", str(path))
    spec = importlib.util.spec_from_file_location("cfo_config", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cfo_config"] = module
    spec.loader.exec_module(module)
    return module


def test_the_settings_land_beside_what_was_already_there(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "model": {"default": "gpt-5.6-sol", "provider": "openai-codex"},
        "platforms": {"plow_chat": {"enabled": True}},
        "cron": {"wrap_response": False},
    }, sort_keys=False))
    module = load(monkeypatch, config)
    assert module.main() == 0
    after = yaml.safe_load(config.read_text())
    assert after["compression"]["threshold_tokens"] == 90000
    assert after["compression"]["proactive_prune_tokens"] == 40000
    # Everything else survives, including the keys the base and the plugin own.
    assert after["model"]["provider"] == "openai-codex"
    assert after["platforms"]["plow_chat"]["enabled"] is True
    assert after["cron"]["wrap_response"] is False


def test_a_value_the_owner_set_is_never_overwritten(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "compression": {"threshold_tokens": 150000, "enabled": True},
    }, sort_keys=False))
    module = load(monkeypatch, config)
    assert module.main() == 0
    after = yaml.safe_load(config.read_text())
    assert after["compression"]["threshold_tokens"] == 150000, "clobbered a choice"
    assert after["compression"]["enabled"] is True
    assert after["compression"]["proactive_prune_tokens"] == 40000, "the absent one"


def test_a_second_boot_changes_nothing(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"model": {"default": "x"}}, sort_keys=False))
    module = load(monkeypatch, config)
    assert module.main() == 0
    once = config.read_text()
    assert module.main() == 0
    assert config.read_text() == once, "rewrote a file it had nothing to add to"


def test_a_config_it_cannot_parse_is_left_alone_and_the_boot_continues(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model: {default: x\n  bad: [\n")
    module = load(monkeypatch, config)
    assert module.main() == 0, "a parse error must not fail the boot"
    assert config.read_text() == "model: {default: x\n  bad: [\n"


def test_a_missing_config_is_not_created(monkeypatch, tmp_path):
    """The base seeds that file. Creating one here would race it, and a
    config.yaml holding nothing but compression settings boots an agent with
    no chat platform in it."""
    config = tmp_path / "config.yaml"
    module = load(monkeypatch, config)
    assert module.main() == 0
    assert not config.exists()


def test_a_config_that_is_not_a_mapping_is_left_alone(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(["not", "a", "mapping"]))
    module = load(monkeypatch, config)
    assert module.main() == 0
    assert yaml.safe_load(config.read_text()) == ["not", "a", "mapping"]


def test_the_write_is_atomic_and_leaves_no_sibling(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"model": {"default": "x"}}))
    module = load(monkeypatch, config)
    assert module.main() == 0
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]
