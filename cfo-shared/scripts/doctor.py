#!/usr/bin/env python3
"""Is this install actually this agent? One command, every silent failure.

Every defect that cost this agent real days failed WITHOUT AN ERROR:

  * the skills mounted at a path the gateway does not read, so a fresh
    install booted, answered the phone, and was a generic Hermes with no
    ledger and no persona;
  * the usage report 401'd into a log nobody opened while the agent sat on a
    public leaderboard at zero;
  * the brief gate was a stale copy from an older deploy;
  * Docker was not running at login and the agent was dark for four days.

None of those show up in `docker ps`. This file is the check a person -- or
the host installing it to verify it -- runs to find out in ten seconds,
inside the container where every path resolves:

    docker exec hermes-cfo sh -c \\
      'python3 "$HERMES_HOME"/skills/cfo-shared/scripts/doctor.py'

Prints one line per check with a mark, then a verdict; `--json` prints the
same as data. Exit code 0 when every check passes, 1 otherwise, so a script
can gate on it. It never modifies anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import money  # noqa: E402

SKILLS = ("cfo-shared", "cfo-log", "cfo-ask", "cfo-simulate", "cfo-brief",
          "cfo-setup", "cfo-import", "cfo-panel")
# Copied into $HERMES_HOME/scripts by the deploy hook; a stale copy runs the
# old schedule or the old report, silently.
DEPLOYED = ("brief_gate.py", "notify_gate.py", "panel.py", "usage_report.sh")
CRON_JOBS = ("cfo-brief", "cfo-panel", "cfo-usage", "cfo-notify")
# A line the SOUL.md this repo ships has and the image's default does not.
SOUL_MARK = "You do not do arithmetic"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def check(name: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail,
            **({"fix": fix} if fix and not ok else {})}


def run(home: Path) -> list[dict]:
    out = []
    skills = home / "skills"
    here = Path(__file__).resolve().parent

    # 1. the home the gateway reads
    out.append(check(
        "home", home.is_dir() and (home / "config.yaml").is_file(),
        f"HERMES_HOME={home}" + ("" if (home / "config.yaml").is_file()
                                 else " -- no config.yaml there"),
        "the gateway reads a different home; see compose.override.yml and "
        "the HERMES_HOME note in README"))

    # 2. the eight skills, where the gateway looks
    present = [s for s in SKILLS if (skills / s / ("SKILL.md" if s != "cfo-shared"
                                                     else "scripts/money.py")).is_file()]
    missing = [s for s in SKILLS if s not in present]
    out.append(check(
        "skills", not missing,
        f"{len(present)}/{len(SKILLS)} under {skills}"
        + (f" -- missing {', '.join(missing)}" if missing else ""),
        "mounts landed elsewhere: `git pull` this repo and agent-mgr, then "
        "`agent-mgr deploy <name>`"))

    # 3. this repo's SOUL.md, not the image's
    soul = home / "SOUL.md"
    try:
        text = soul.read_text(errors="replace")
        has_mark = SOUL_MARK in text
        out.append(check(
            "soul", has_mark,
            "the cfo SOUL.md is in place" if has_mark else "not this agent’s",
            "the SOUL.md bind is not landing; see compose.override.yml"))
    except OSError as exc:
        # A single-file bind mount keeps pointing at the inode it was made
        # from. `git pull` writes a new file, the old one is unlinked, and the
        # container is left with a path that stats and cannot be opened --
        # the gateway then boots with no persona at all. Only a restart
        # re-binds it, and nothing else reports it.
        out.append(check(
            "soul", False,
            f"{soul} cannot be read ({exc.strerror}) -- the host file was "
            "replaced under a single-file bind",
            "restart the container: `agent-mgr restart <name>` (or `docker "
            "restart <container>`) re-binds it"))

    # 4. the ledger
    try:
        con = money.connect()
        st = money.status(con)
        cfg = st["configured"]
        out.append(check(
            "ledger", True,
            f"{money.db_path()} · schema {money.get_cfg(con, 'schema_version')}"
            f" · {st['transactions']} rows"
            + (" · all sample data" if st["all_data_is_demo"] else "")))
        out.append(check(
            "timezone", bool(cfg["timezone"]),
            cfg["timezone"] or "not set -- days are being stamped in UTC",
            "ask the owner what city they are in; `money.py config timezone`"))
        out.append(check(
            "currency", bool(cfg["currency"]), cfg["currency"] or "not set",
            "`money.py config currency BRL|USD|EUR|GBP`"))
        lang = cfg["language"] or f"{money.language_of(con)} (from the currency)"
        out.append(check("language", True, lang))
        hours = ", ".join(f"{slot} {cfg[key] if cfg[key] is not None else 'off'}"
                          for slot, key, _ in money.BRIEF_SLOTS)
        out.append(check("brief hours", True, f"{hours} (owner's zone)"))
        # Informational: a fresh install is not set up yet, and the agent
        # asks for this in the chat. Not a failure of the install.
        out.append(check(
            "setup", True,
            "ready" if st["ready"] else f"not yet -- next: {st['next_step']}"))
    except Exception as exc:                     # noqa: BLE001
        out.append(check("ledger", False, f"{type(exc).__name__}: {exc}",
                         "the data dir is not readable or writable"))
        con = None

    # 5. the gate state: when each slot last opened
    gate = money.data_dir() / "brief_gate.json"
    try:
        opened = json.loads(gate.read_text())
        out.append(check("brief last opened", True,
                         ", ".join(f"{k} {v}" for k, v in sorted(opened.items()))
                         or "never yet"))
    except FileNotFoundError:
        out.append(check("brief last opened", True,
                         "never yet -- opens at the owner's next brief hour"))
    except Exception as exc:                     # noqa: BLE001
        out.append(check("brief last opened", False, f"unreadable: {exc}"))

    # 6. the deployed copies match the repo
    for name in DEPLOYED:
        src, dst = here / name, home / "scripts" / name
        if not dst.is_file():
            out.append(check(f"scripts/{name}", False, "not deployed",
                             "`agent-mgr deploy <name>` copies it"))
        elif src.is_file() and _sha(src) != _sha(dst):
            out.append(check(f"scripts/{name}", False,
                             "STALE -- differs from the repo copy",
                             "`agent-mgr deploy <name>`, or copy it by hand"))
        else:
            out.append(check(f"scripts/{name}", True, "current"))

    # 7. the cron rows
    jobs_file = home / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_file.read_text())
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        names = {j.get("name"): j for j in jobs if isinstance(j, dict)}
        missing = [n for n in CRON_JOBS if n not in names]
        off = [n for n in CRON_JOBS if n in names and names[n].get("enabled") is False]
        last = {n: (names[n].get("last_run") or "never")[:16] for n in CRON_JOBS if n in names}
        out.append(check(
            "cron", not missing and not off,
            ", ".join(f"{n} last {last[n]}" for n in last)
            + (f" -- missing {', '.join(missing)}" if missing else "")
            + (f" -- disabled {', '.join(off)}" if off else ""),
            "`agent-mgr cron-sync <name>`"))
    except FileNotFoundError:
        out.append(check("cron", False, f"no {jobs_file}",
                         "`agent-mgr cron-sync <name>`"))
    except Exception as exc:                     # noqa: BLE001
        out.append(check("cron", False, f"unreadable: {exc}"))

    # 8. the panel
    panel = money.data_dir() / "panel" / "index.html"
    if panel.is_file():
        age = (datetime.now(timezone.utc)
               - datetime.fromtimestamp(panel.stat().st_mtime, timezone.utc))
        mins = int(age.total_seconds() // 60)
        out.append(check("panel", mins <= 30,
                         f"{panel} · written {mins} min ago",
                         "the cfo-panel cron row is not running; "
                         "`agent-mgr cron-sync <name>`"))
    else:
        out.append(check("panel", True,
                         "not rendered yet -- appears after the first ledger "
                         "write or the next panel tick"))

    # 8b. the push channel: is the Mac forwarding anything?
    inbox = home / "inbox" / "notifications.jsonl"
    if inbox.is_file():
        try:
            lines = [l for l in inbox.read_text(errors="replace").splitlines() if l.strip()]
            last_ts = (json.loads(lines[-1]).get("ts") or "?")[:16] if lines else "never"
            out.append(check("push inbox", True,
                             f"{len(lines)} notification(s) forwarded, last {last_ts}"))
        except Exception as exc:                 # noqa: BLE001
            out.append(check("push inbox", False, f"unreadable: {exc}"))
    else:
        out.append(check("push inbox", True,
                         "not set up -- optional; scripts/install-notify.sh on the Mac"))

    # 9. the Agent Index: registered, and the last report landed
    client = home / "scripts" / "agent_index_client.py"
    if client.is_file():
        try:
            env = {**os.environ, "HOME": str(home)}
            r = subprocess.run([sys.executable, str(client), "status"],
                               capture_output=True, text=True, timeout=20, env=env)
            words = {0: "registered", 3: "not registered",
                     2: "state present but unreadable -- do NOT re-register"}
            out.append(check("index registration", r.returncode == 0,
                             words.get(r.returncode, f"exit {r.returncode}")
                             + ((" · " + r.stdout.strip().splitlines()[-1])
                                if r.stdout.strip() else ""),
                             "the hourly cfo-usage job registers on its next tick; "
                             "read logs/agent-index.log"))
        except Exception as exc:                 # noqa: BLE001
            out.append(check("index registration", False, f"{type(exc).__name__}: {exc}"))
    else:
        out.append(check("index registration", False, "client not deployed",
                         "`agent-mgr deploy <name>` fetches it"))
    log = home / "logs" / "agent-index.log"
    try:
        lines = [l for l in log.read_text(errors="replace").splitlines() if l.strip()]
        tail = lines[-1].strip() if lines else ""
        landed = any(" 200 " in l or l.strip().startswith("200") for l in lines[-6:])
        out.append(check("index report", landed,
                         tail[:110] if tail else "log is empty",
                         "reports are failing; the last lines say why"))
    except FileNotFoundError:
        out.append(check("index report", True,
                         "no report yet -- the cfo-usage job writes the first "
                         "at :17 past the hour"))

    if con is not None:
        con.close()
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="doctor",
                                description="check that this install is this agent")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    checks = run(home)
    ok = all(c["ok"] for c in checks)
    if args.json:
        print(json.dumps({"ok": ok, "home": str(home), "checks": checks},
                         ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = "✓" if c["ok"] else "✗"
            print(f"  {mark} {c['check']:<20} {c['detail']}")
            if not c["ok"] and c.get("fix"):
                print(f"    → {c['fix']}")
        print("\n  " + ("all good -- this is cfo, and it is reporting"
                        if ok else "something above needs a look"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
