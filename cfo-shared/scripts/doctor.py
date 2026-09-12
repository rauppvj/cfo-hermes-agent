#!/usr/bin/env python3
"""Is this install actually this agent? One command, every silent failure.

Every defect that cost this agent real days failed WITHOUT AN ERROR:

  * the skills mounted at a path the gateway does not read, so a fresh
    install booted, answered the phone, and was a generic Hermes with no
    ledger and no persona;
  * the usage report 401'd into a log nobody opened while the agent sat on a
    public leaderboard at zero;
  * the brief gate was a stale copy from an older deploy;
  * the CSV the owner asked for was dropped on the way out, because the
    current boot contract puts the whole home under a path the gateway will
    not attach from;
  * Docker was not running at login and the agent was dark for four days.

None of those show up in `docker ps`. This file is the check a person -- or
the host installing it to verify it -- runs to find out in ten seconds,
inside the container where every path resolves:

    docker compose exec agent \\
        python3 /var/lib/hermes/skills/cfo-shared/scripts/doctor.py

Prints one line per check with a mark, then a verdict; `--json` prints the
same as data. Exit code 0 when every check passes, 1 otherwise, so a script
can gate on it. It never modifies anything.

## Two contracts

This agent has run under two deployers, and a check whose FIX names the wrong
one is worse than no check: it sends whoever is reading it to a command that
does not apply to their install and cannot help them.

  * **plow-agents** (current) -- the repo is an image: `plow-agents mint
    <line>` writes a credential, `docker compose up --build` boots it, the
    skills and the persona come from image layers, two jobs run as supervised
    services and two as gated cron rows.
  * **agent-mgr** (deprecated upstream on 2026-09-03, still under the one
    instance that was installed with it) -- the repo is a checkout, mounted
    into a home on the Mac, deployed and scheduled by an external CLI.

So the contract is DETECTED, once, and every check that differs between them
reads that answer. The marker is a file only the current image ships.
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
# A line the persona this repo ships has and the image's base does not.
SOUL_MARK = "You do not do arithmetic"

# Where the current image keeps the copies a supervisor runs, and the marker
# that says this container was built from this repo's Dockerfile.
PLOW_DIR = Path("/opt/plow")
IMAGE_MARKER = PLOW_DIR / "cfo-schedule.py"
IMAGE_SCRIPTS = PLOW_DIR / "cfo-shared" / "scripts"
IMAGE_CLIENT = PLOW_DIR / "agent-index-client.py"
# s6's own state, read rather than guessed at. Named here so a test can point
# them somewhere else; nothing else in this file knows where s6 keeps things.
S6_SVSTAT = "/command/s6-svstat"
SERVICE_DIR = "/run/service"

# What each contract schedules, and how.
JOBS = {
    # contract:        cron rows                   supervised services
    "plow-agents": (("cfo-brief", "cfo-notify"),
                    ("agent-index", "cfo-panel", "cfo-schedule")),
    "agent-mgr": (("cfo-brief", "cfo-panel", "cfo-usage", "cfo-notify"), ()),
}
# Staged into $HERMES_HOME/scripts, where hermes will run a cron script from.
# A stale copy runs the old schedule or the old report, silently.
STAGED = {
    "plow-agents": ("brief_gate.py", "notify_gate.py"),
    "agent-mgr": ("brief_gate.py", "notify_gate.py", "panel.py",
                  "usage_report.sh"),
}


def contract() -> str:
    return "plow-agents" if IMAGE_MARKER.is_file() else "agent-mgr"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _mounted(path: Path) -> bool:
    """Is this path a mount point -- i.e. does the host see it too?

    Read from the kernel's own table rather than guessed at. The panel exists
    to be looked at, and a panel written inside a container with no mount
    under it is a page nobody can open: the one failure that looks completely
    healthy from in here.
    """
    try:
        target = str(path.resolve())
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            return any(line.split()[4] == target for line in handle)
    except (OSError, IndexError):
        return False


def check(name: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail,
            **({"fix": fix} if fix and not ok else {})}


def run(home: Path) -> list[dict]:
    out = []
    how = contract()
    skills = home / "skills"
    here = Path(__file__).resolve().parent
    rebuild = ("`docker compose up --build -d` rebuilds this image"
               if how == "plow-agents" else "`agent-mgr deploy <name>`")

    out.append(check("contract", True, how + (
        " (this repo's image)" if how == "plow-agents"
        else " (deprecated upstream -- see legacy/README.md)")))

    # 1. the home the gateway reads
    out.append(check(
        "home", home.is_dir() and (home / "config.yaml").is_file(),
        f"HERMES_HOME={home}" + ("" if (home / "config.yaml").is_file()
                                 else " -- no config.yaml there"),
        "the gateway reads a different home than this check does; on the "
        "current contract the image sets both, so a mismatch here means "
        "HERMES_HOME was overridden where the container is started"))

    # 2. the eight skills, where the gateway looks
    present = [s for s in SKILLS if (skills / s / ("SKILL.md" if s != "cfo-shared"
                                                   else "scripts/money.py")).is_file()]
    missing = [s for s in SKILLS if s not in present]
    out.append(check(
        "skills", not missing,
        f"{len(present)}/{len(SKILLS)} under {skills}"
        + (f" -- missing {', '.join(missing)}" if missing else ""),
        "the agent boots and answers without these -- it is simply not this "
        "agent. " + rebuild))

    # 3. this agent's identity, not the base's
    soul = home / "SOUL.md"
    try:
        text = soul.read_text(errors="replace")
        has_mark = SOUL_MARK in text
        out.append(check(
            "soul", has_mark,
            "the cfo persona is in place" if has_mark else "not this agent's",
            "plow-init composes SOUL.md from the base persona plus "
            "runtime/persona.md on every boot; a missing mark means the image "
            "was built without it. " + rebuild if how == "plow-agents" else
            "the SOUL.md bind is not landing; see legacy/compose.override.yml"))
    except OSError as exc:
        # Legacy only: a single-file bind mount keeps pointing at the inode it
        # was made from. `git pull` writes a new file, the old one is
        # unlinked, and the container is left with a path that stats and
        # cannot be opened -- the gateway then boots with no persona at all.
        out.append(check(
            "soul", False,
            f"{soul} cannot be read ({exc.strerror})"
            + (" -- the host file was replaced under a single-file bind"
               if how == "agent-mgr" else ""),
            "restart the container; that re-binds it"))

    # 4. the ledger
    con = None
    try:
        con = money.connect()
        st = money.status(con)
        cfg = st["configured"]
        out.append(check(
            "ledger", True,
            f"{money.db_path()} · schema {money.get_cfg(con, 'schema_version')}"
            f" · {st['transactions']} rows"
            + (" · all sample data" if st["all_data_is_demo"] else "")))
        # A fresh install has neither, and that is not a defect: the agent
        # asks for both in the first conversation. What IS a defect is rows in
        # the ledger with no zone, because every one of them was stamped in
        # UTC -- an evening purchase filed on the wrong day, and on the 31st in
        # the wrong month. So the same fact is informational until there is
        # data to have got wrong, and a failure after. A host verifying a
        # fresh install should see no ✗ for a question nobody has been asked.
        fresh = st["transactions"] == 0
        out.append(check(
            "timezone", bool(cfg["timezone"]) or fresh,
            cfg["timezone"] or ("not set yet -- the agent asks on first contact"
                                if fresh else
                                "not set -- these rows were stamped in UTC"),
            "ask the owner what city they are in; `money.py config timezone`"))
        out.append(check(
            "currency", bool(cfg["currency"]) or fresh,
            cfg["currency"] or ("not set yet -- the agent asks on first contact"
                                if fresh else "not set"),
            "`money.py config currency BRL|USD|EUR|GBP`"))
        lang = cfg["language"] or f"{money.language_of(con)} (from the currency)"
        out.append(check("language", True, lang))
        hours = ", ".join(f"{slot} {cfg[key] if cfg[key] is not None else 'off'}"
                          for slot, key, _ in money.BRIEF_SLOTS)
        out.append(check("brief hours", True, f"{hours} (owner's zone)"))
        # Informational: a fresh install is not set up yet, and the agent asks
        # for this in the chat. Not a failure of the install.
        out.append(check(
            "setup", True,
            "ready" if st["ready"] else f"not yet -- next: {st['next_step']}"))
    except Exception as exc:                     # noqa: BLE001
        out.append(check("ledger", False, f"{type(exc).__name__}: {exc}",
                         "the data dir is not readable or writable"))

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

    # 6. what the scheduler runs, against the copy it was staged from
    source = IMAGE_SCRIPTS if how == "plow-agents" else here
    for name in STAGED[how]:
        src, dst = source / name, home / "scripts" / name
        if not dst.is_file():
            out.append(check(
                f"scripts/{name}", False, "not staged",
                "the cfo-schedule service copies it at boot; "
                "`docker compose logs agent | grep cfo-schedule` says why not"
                if how == "plow-agents" else "`agent-mgr deploy <name>` copies it"))
        elif src.is_file() and _sha(src) != _sha(dst):
            out.append(check(f"scripts/{name}", False,
                             "STALE -- differs from the copy this image ships",
                             "restart the container; cfo-schedule re-stages it"
                             if how == "plow-agents" else "`agent-mgr deploy <name>`"))
        else:
            out.append(check(f"scripts/{name}", True, "current"))

    # 7. the schedule: cron rows for what must wake the model
    rows, services = JOBS[how]
    jobs_file = home / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_file.read_text())
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        names = {j.get("name"): j for j in jobs if isinstance(j, dict)}
        absent = [n for n in rows if n not in names]
        off = [n for n in rows if n in names and names[n].get("enabled") is False]
        last = {n: (names[n].get("last_run") or "never")[:16] for n in rows if n in names}
        out.append(check(
            "cron", not absent and not off,
            ", ".join(f"{n} last {last[n]}" for n in last)
            + (f" -- missing {', '.join(absent)}" if absent else "")
            + (f" -- disabled {', '.join(off)}" if off else ""),
            "the cfo-schedule service registers them within five minutes of "
            "boot, and needs the home chat to exist first; "
            "`docker compose logs agent | grep cfo-schedule`"
            if how == "plow-agents" else "`agent-mgr cron-sync <name>`"))
    except FileNotFoundError:
        out.append(check(
            "cron", False, f"no {jobs_file} -- nothing is scheduled",
            "the cfo-schedule service writes it; "
            "`docker compose logs agent | grep cfo-schedule`"
            if how == "plow-agents" else "`agent-mgr cron-sync <name>`"))
    except Exception as exc:                     # noqa: BLE001
        out.append(check("cron", False, f"unreadable: {exc}"))

    # 7b. the supervised half, on the current contract
    if services:
        # s6 keeps one directory per service, and `s6-svstat` reads it. The
        # path is the supervision tree's, not the source tree's.
        states = {}
        for name in services:
            try:
                done = subprocess.run([S6_SVSTAT, f"{SERVICE_DIR}/{name}"],
                                      capture_output=True, text=True, timeout=10)
                states[name] = (done.stdout or done.stderr).strip().split(",")[0] or "?"
            except Exception as exc:             # noqa: BLE001
                states[name] = f"{type(exc).__name__}"
        up = [n for n, s in states.items() if s.startswith("up")]
        out.append(check(
            "services", len(up) == len(services),
            ", ".join(f"{n} {s}" for n, s in states.items()),
            "a service that is down reports nothing and draws nothing; "
            "`docker compose logs agent` carries its reason"))

    # 8. the panel, and whether anyone outside this container can open it
    panel = Path(os.environ.get("CFO_PANEL") or money.data_dir() / "panel" / "index.html")
    if panel.is_file():
        age = (datetime.now(timezone.utc)
               - datetime.fromtimestamp(panel.stat().st_mtime, timezone.utc))
        mins = int(age.total_seconds() // 60)
        out.append(check("panel", mins <= 30,
                         f"{panel} · written {mins} min ago",
                         "the panel is not being redrawn; check the cfo-panel "
                         "service" if how == "plow-agents"
                         else "`agent-mgr cron-sync <name>`"))
    else:
        out.append(check("panel", True,
                         "not rendered yet -- appears after the first ledger "
                         "write or the next panel tick"))
    if how == "plow-agents":
        visible = _mounted(panel.parent)
        out.append(check(
            "panel reachable", visible,
            f"{panel.parent} is mounted from the host -- open panel/index.html"
            if visible else
            f"{panel.parent} is inside the container only",
            "compose.yml mounts ./panel there; without it the page is redrawn "
            "every ten minutes where nobody can open it"))

    # 8b. the push channel: is the Mac forwarding anything?
    inbox = Path(os.environ.get("CFO_INBOX") or home / "inbox" / "notifications.jsonl")
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

    # 9. the Agent Index: is this install registered, and can it report?
    client = IMAGE_CLIENT if how == "plow-agents" else home / "scripts" / "agent_index_client.py"
    agent_id = os.environ.get("AGENT_ID", "")
    if how == "plow-agents":
        out.append(check(
            "index id", bool(agent_id), agent_id or "AGENT_ID is not set",
            "the reporter stands down without it rather than guessing a name: "
            "set `AGENT_ID: cfo` under environment: in compose.yml"))
    if client.is_file():
        try:
            env = {**os.environ, "HOME": str(home), "HERMES_HOME": str(home)}
            r = subprocess.run([sys.executable, str(client), "status"],
                               capture_output=True, text=True, timeout=20, env=env)
            words = {0: "registered", 3: "not registered yet",
                     2: "state present but unreadable -- do NOT re-register"}
            out.append(check(
                "index registration", r.returncode == 0,
                words.get(r.returncode, f"exit {r.returncode}")
                + ((" · " + r.stdout.strip().splitlines()[-1])
                   if r.stdout.strip() else ""),
                "the agent-index service registers on its next pass (5 min); "
                "`docker compose logs agent | grep agent-index`"
                if how == "plow-agents" else
                "the hourly cfo-usage job registers on its next tick; "
                "read logs/agent-index.log"))
        except Exception as exc:                 # noqa: BLE001
            out.append(check("index registration", False, f"{type(exc).__name__}: {exc}"))
    else:
        out.append(check("index registration", False, f"no client at {client}",
                         rebuild))

    # What the next report would carry. On the current contract there is no
    # log file to tail -- the reporter is a supervised service, so its output
    # is the container's -- and "registered" alone does not prove there is
    # anything to send. --dry-run collects and sends nothing.
    registered = any(c["check"] == "index registration" and c["ok"] for c in out)
    if how == "plow-agents" and client.is_file() and agent_id and registered:
        try:
            env = {**os.environ, "HOME": str(home), "HERMES_HOME": str(home)}
            r = subprocess.run([sys.executable, str(client), "--agent", agent_id,
                                "--dry-run", "--days", "2"],
                               capture_output=True, text=True, timeout=30, env=env)
            line = next((l.strip() for l in r.stdout.splitlines()
                         if l.strip().startswith("agent=")), "")
            failed = "COLLECTOR FAILED" in r.stdout
            out.append(check(
                "index collection", not failed and r.returncode == 0,
                line or (r.stdout.strip().splitlines() or ["no output"])[-1][:110],
                "a collector that fails stops the whole report rather than "
                "sending a partial one; the line above says which"))
        except Exception as exc:                 # noqa: BLE001
            out.append(check("index collection", False, f"{type(exc).__name__}: {exc}"))
    elif how == "plow-agents":
        # Nothing to collect FOR until this install has a key, and running the
        # client without one exits before it collects. Two ✗ lines for one
        # cause is a reader chasing the second.
        pass
    else:
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
                             "no report yet -- the cfo-usage job writes the "
                             "first at :17 past the hour"))

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
        print(json.dumps({"ok": ok, "home": str(home), "contract": contract(),
                          "checks": checks}, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = "✓" if c["ok"] else "✗"
            print(f"  {mark} {c['check']:<20} {c['detail']}")
            if not c["ok"] and c.get("fix"):
                print(f"    → {c['fix']}")
        verdict = ("all good -- this is cfo, and it is reporting"
                   if ok else "something above needs a look")
        if ok and any(c["check"] == "setup" and "not yet" in c["detail"]
                      for c in checks):
            verdict = ("all good -- this is cfo. Text it to set it up: it asks "
                       "two questions and starts logging.")
        print("\n  " + verdict)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
