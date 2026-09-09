#!/usr/bin/env python3
"""Runs ON THE MAC: the bank's push notifications, read off Notification Center.

iOS exposes no notification trigger to Shortcuts, and most Brazilian banks
alert by push, not SMS -- so a purchase on the physical card, or on the
Wallet card used as DÉBITO, reaches the phone as a notification and nothing
else. macOS can see those: with iPhone Mirroring set up and "Allow
notifications from iPhone" on (System Settings > Notifications), every one
of them lands in the Mac's own Notification Center, and Notification Center
keeps them in a SQLite database:

    ~/Library/Group Containers/group.com.apple.usernoted/db2/db

This script reads that database every minute (launchd; see
scripts/install-notify.sh), keeps the notifications that carry an amount,
and appends them as JSON lines to

    ~/.hermes-<name>/inbox/notifications.jsonl

-- inside the instance's home, which is bind-mounted into the container,
where notify_gate.py picks them up. Nothing here talks to the network,
nothing here parses money; it moves text from one file on this Mac to
another file on this Mac.

Runs on the system /usr/bin/python3, which may be 3.9: nothing newer is
used, and nothing from the rest of the engine is imported except
bankalert.py, which keeps the same promise.

The database is protected by macOS: the interpreter running this needs
Full Disk Access (System Settings > Privacy & Security > Full Disk Access).
Without it the read fails with "authorization denied" or "Operation not
permitted", which this script turns into one clear line in the log.

Starts from NOW on first run: the notifications already sitting in the
database are last week's purchases, and the statement import is the right
way to get those. `--backfill N` overrides that once.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bankalert  # noqa: E402

DB = Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"
COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

FDA_HINT = ("cannot read Notification Center's database -- give Full Disk Access "
            "to {exe} (System Settings > Privacy & Security > Full Disk Access > +) "
            "and this starts working on the next minute")


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def unpack(data: bytes) -> dict:
    """title / subtitle / body out of the record's plist. Missing keys are
    empty strings; an unreadable blob is an empty notification, not a crash."""
    try:
        d = plistlib.loads(data)
    except Exception:                            # noqa: BLE001
        return {"title": "", "subtitle": "", "body": ""}
    req = d.get("req", {}) if isinstance(d, dict) else {}
    return {"title": str(req.get("titl", "") or ""),
            "subtitle": str(req.get("subt", "") or ""),
            "body": str(req.get("body", "") or "")}


def records(con: sqlite3.Connection, since_rec_id: int) -> list:
    return con.execute(
        "SELECT r.rec_id, r.delivered_date, r.data, a.identifier"
        " FROM record r LEFT JOIN app a ON a.app_id = r.app_id"
        " WHERE r.rec_id > ? ORDER BY r.rec_id", (since_rec_id,)).fetchall()


def max_rec_id(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT COALESCE(MAX(rec_id), 0) AS m FROM record").fetchone()
    return int(row["m"])


def delivered_iso(cocoa_seconds) -> str:
    try:
        return (COCOA_EPOCH + timedelta(seconds=float(cocoa_seconds))).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scan(db: Path, home: Path, backfill: int = 0) -> dict:
    """Read new records, append the money-shaped ones to the inbox, advance."""
    state_file = home / "cfo" / "notify_watch.json"
    inbox = home / "inbox" / "notifications.jsonl"
    try:
        state = json.loads(state_file.read_text())
    except (FileNotFoundError, ValueError):
        state = {}

    con = open_db(db)
    try:
        last = state.get("last_rec_id")
        if last is None:
            # First run: start from now, unless asked to look back.
            last = max(0, max_rec_id(con) - backfill)
        rows = records(con, int(last))
    finally:
        con.close()

    kept = 0
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as out:
        for r in rows:
            fields = unpack(r["data"])
            text = " ".join(v for v in fields.values() if v).strip()
            if not text or bankalert.parse(text)["amount_cents"] is None:
                continue
            out.write(json.dumps({
                "ts": delivered_iso(r["delivered_date"]),
                "app": r["identifier"] or "",
                **fields,
            }, ensure_ascii=False) + "\n")
            kept += 1
    new_last = rows[-1]["rec_id"] if rows else int(last)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_rec_id": int(new_last),
                               "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    tmp.replace(state_file)
    return {"seen": len(rows), "kept": kept, "last_rec_id": int(new_last)}


def dump(db: Path, n: int) -> int:
    con = open_db(db)
    try:
        rows = con.execute(
            "SELECT r.rec_id, r.delivered_date, r.data, a.identifier FROM record r"
            " LEFT JOIN app a ON a.app_id = r.app_id ORDER BY r.rec_id DESC LIMIT ?",
            (n,)).fetchall()
    finally:
        con.close()
    for r in rows:
        f = unpack(r["data"])
        text = " | ".join(v for v in (f["title"], f["subtitle"], f["body"]) if v)
        mark = "$" if bankalert.parse(text)["amount_cents"] is not None else " "
        print(f"{mark} {r['rec_id']:>7} {delivered_iso(r['delivered_date'])[:16]} "
              f"{(r['identifier'] or '?')[:40]:<40} {text[:110]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="notify_watch", description="forward bank notifications to the agent's inbox")
    p.add_argument("--home", default=str(Path.home() / ".hermes-cfo"),
                   help="the instance's home on this Mac (default ~/.hermes-cfo)")
    p.add_argument("--db", default=str(DB))
    p.add_argument("--backfill", type=int, default=0,
                   help="on first run, also read this many of the newest notifications")
    p.add_argument("--dump", type=int, default=None, metavar="N",
                   help="print the newest N notifications and exit ($ marks an amount)")
    p.add_argument("--check", action="store_true",
                   help="say whether the database is readable, and exit")
    args = p.parse_args(argv)

    db, home = Path(args.db), Path(args.home).expanduser()
    try:
        if args.check:
            con = open_db(db)
            n = max_rec_id(con)
            con.close()
            print(f"ok: Notification Center database readable, {n} records")
            return 0
        if args.dump is not None:
            return dump(db, args.dump)
        result = scan(db, home, args.backfill)
        if result["kept"]:
            print(f"{datetime.now().isoformat(timespec='seconds')} forwarded "
                  f"{result['kept']} of {result['seen']} new notifications")
        return 0
    except (sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError, OSError) as exc:
        msg = str(exc)
        if "authorization denied" in msg or "unable to open" in msg or "not permitted" in msg.lower():
            print(FDA_HINT.format(exe=sys.executable), file=sys.stderr)
        else:
            print(f"notify_watch: {exc}", file=sys.stderr)
        # Zero for launchd, which would otherwise back off a "failing" job;
        # non-zero for --check, whose whole job is to be gated on. The first
        # installer printed "readable" over an exit code of 0 with the real
        # answer on a stderr it had silenced.
        return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
