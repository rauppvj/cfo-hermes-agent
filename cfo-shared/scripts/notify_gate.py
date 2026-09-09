#!/usr/bin/env python3
"""The cron gate for bank notifications the Mac forwarded.

`notify_watch.py` runs ON THE MAC, reads the iPhone notifications mirrored
into the Mac's own Notification Center, and appends the ones that carry an
amount to `$HERMES_HOME/inbox/notifications.jsonl` -- a file inside the
instance's home, which is the one directory both machines share. This file
runs IN THE CONTAINER every five minutes and does the rest:

  * reads what is new since its last offset;
  * turns each alert into fields with bankalert.py -- code, not a model, so
    an amount is either in the text or the line is not logged;
  * records it through money.record_forwarded, which is where a purchase the
    Wallet tap already logged is corrected to the bank's DÉBITO/CRÉDITO
    instead of being written twice;
  * and only THEN wakes the agent, with what was done, so it can say it in
    one line. Nothing new means `{"wakeAgent": false}` and no model run.

Same two guarantees as brief_gate.py: it never exits non-zero (a crash would
not silence it, it would fire the agent every five minutes with nothing),
and the offset advances even when a line could not be read, so one bad line
cannot be re-read forever.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

for _candidate in (Path(__file__).resolve().parent,
                   Path(os.environ.get("HERMES_HOME", "/opt/data"),
                        "skills/cfo-shared/scripts"),
                   Path("/opt/data/skills/cfo-shared/scripts")):
    if (_candidate / "money.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

import bankalert  # noqa: E402
import money      # noqa: E402
import statement  # noqa: E402  (the merchant rules)


def inbox_path() -> Path:
    return Path(os.environ.get("HERMES_HOME", "/opt/data")) / "inbox" / "notifications.jsonl"


def state_path() -> Path:
    return money.data_dir() / "notify_gate.json"


def load_offset() -> int:
    try:
        return int(json.loads(state_path().read_text()).get("offset", 0))
    except (FileNotFoundError, ValueError, AttributeError):
        return 0


def save_offset(offset: int) -> None:
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"offset": offset}))
    tmp.replace(state_path())


def read_new(path: Path, offset: int) -> tuple[list[dict], int]:
    """Lines appended since `offset`, and the new offset. A truncated or
    rotated file (smaller than the offset) is read from the start."""
    if not path.is_file():
        return [], offset
    size = path.stat().st_size
    if size < offset:
        offset = 0
    with path.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    lines = []
    for raw in chunk.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw.decode("utf-8", errors="replace")))
        except ValueError:
            lines.append({"body": raw.decode("utf-8", errors="replace")})
    return lines, offset + len(chunk)


def category_for(con, merchant: str) -> str:
    learned = money.categorize_learned(merchant, money.learned_categories(con))
    return learned or statement.categorize(merchant)


def handle(con, item: dict) -> str:
    """One notification -> one line for the agent, with the ledger updated."""
    text = " ".join(str(item.get(k) or "") for k in ("title", "subtitle", "body")).strip()
    app = item.get("app") or ""
    p = bankalert.parse(text)
    cur = money.currency_of(con)
    if not p["ok"]:
        if p["event"] in ("declined", "invoice_closed"):
            return f"ℹ not logged ({p['event'].replace('_', ' ')}): {text[:100]}"
        return f"? no amount could be read -- not logged: {text[:100]}"
    merchant = p["merchant"] or app or "bank alert"
    got = money.record_forwarded(
        con, p["amount_cents"], p["kind"], category_for(con, merchant),
        note=merchant, source="push", method=p["method"])
    amount = money.fmt(p["amount_cents"], cur)
    if got["skipped"]:
        tail = f", method corrected to {got['method']}" if got["method_updated"] else ""
        return (f"= {amount} · {merchant}: already logged from the "
                f"{got['already_logged_from']} (id {got['duplicate_of']}){tail}")
    return (f"✓ logged id {got['id']}: {amount} · {merchant} · {got['category']}"
            f" · {got['kind']} · {got['method']}")


def main() -> int:
    try:
        con = money.connect()
        offset = load_offset()
        items, new_offset = read_new(inbox_path(), offset)
        if not items:
            print(json.dumps({"wakeAgent": False}))
            return 0
        lines = [handle(con, item) for item in items]
        save_offset(new_offset)
        if any(l.startswith("✓") or "corrected" in l for l in lines):
            money.refresh_panel(con)
        code = money.language_of(con)
        name = money.LANGUAGE_NAMES.get(code, code)
        print("Bank notifications forwarded from the owner's Mac. Lines marked ✓ "
              "and = were ALREADY handled by code -- do not log them again:")
        print("\n".join(lines))
        print(f"Write one line, in {name} (the owner's language), saying what was "
              "recorded: amount, merchant, category, and débito/crédito when it "
              "matters. A ? line carries no amount: say nothing about it unless "
              "you can read a real amount in it yourself, in which case log it "
              "with `money.py add ... --source push`. Never invent a figure.")
        return 0
    except Exception:                            # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"wakeAgent": False}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
