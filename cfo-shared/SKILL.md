---
name: cfo-shared
description: The engine every cfo-* skill calls — the ledger (money.py), the statement reader (statement.py), the wall panel (panel.py), the bank-alert parser (bankalert.py) and the install check (doctor.py). Not a task; nothing here is invoked on its own. Each cfo-* SKILL.md names the command it needs.
---

# cfo-shared — the engine the other skills call

Every other cfo skill reaches this directory by absolute path —
`$HERMES_HOME/skills/cfo-shared/scripts/money.py` — so it has to land beside
them in the home's `skills/`.

**This file is what makes that happen.** The runtime's boot reconcile copies a
shipped directory into the home only when the directory carries a `SKILL.md`,
and it finds them by looking for that filename. Without this file the seven
task skills seed and this one does not: the agent boots, answers the phone,
reads a skill that tells it to run `money.py` — and there is no `money.py`,
under a home whose `skills/` looks otherwise complete. Nothing reports it.
That was the shape of the failure on 2026-09-12, caught by booting the image
and listing the home rather than by reading the Dockerfile.

There is no task here. The commands, and which skill owns each:

| script | what it is | called by |
|---|---|---|
| `scripts/money.py` | the ledger: every read and every write, integer cents in, formatted strings out | every skill |
| `scripts/statement.py` | a bank statement or card invoice, CSV or PDF, into rows | `cfo-import` |
| `scripts/panel.py` | the whole ledger as one HTML page, no model in it | `cfo-panel`, and a supervised service every 10 min |
| `scripts/bankalert.py` | one bank notification into amount, merchant, method | `cfo-log`, and `notify_gate.py` |
| `scripts/brief_gate.py` | is this hour a brief hour where the OWNER lives | the `cfo-brief` cron row |
| `scripts/notify_gate.py` | is there a forwarded bank notification worth waking for | the `cfo-notify` cron row |
| `scripts/doctor.py` | is this install actually this agent — every silent failure, one command | a person, after an install |
| `scripts/seed_demo.py` | sample data, marked `source: demo` so nothing is mistaken for the owner's | `cfo-setup` |
| `scripts/pdf_text.py` | a PDF to text, under `uv run --with pypdf` | `statement.py` |
| `scripts/usage_report.sh` | the Agent Index report, on the deprecated deployer only | the legacy `cfo-usage` cron row |

Two rules that live in the engine rather than in a prompt, because a prompt
cannot enforce them:

- **Every number the agent says comes from a field here.** `money.py` answers
  JSON with `_fmt` strings already formatted for the owner's currency and
  locale, so nothing downstream has to add, divide or round. A model that does
  arithmetic will one day do it wrong in the same confident voice.
- **A failure answers `{"ok": false, "error": ..., "say": ...}`** — the `say`
  field is the one sentence meant for the owner. The rest is for the agent.

`money.py --help` and `statement.py --help` are current and worth reading
before guessing at a subcommand.
