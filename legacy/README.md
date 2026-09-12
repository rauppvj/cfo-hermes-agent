# The deprecated deployer

Everything in this directory belongs to `agent-mgr`, which **upstream
deprecated on 2026-09-03** in favour of
[`plow-pbc/plow-agents`](https://github.com/plow-pbc/plow-agents). Nothing
here is part of installing this agent. Read the root
[`README.md`](../README.md) or [`docs/INSTALL.md`](../docs/INSTALL.md) for that.

It is kept, rather than deleted, for one reason: **one instance is still
running on it** — the author's own, installed on 2026-08-31, holding a real
person's ledger and the line their phone texts. Migrating a live instance
mid-hackathon would mean moving a credential minted by an activation trail
that upstream has since retired, onto a new contract, with the agent's usage
being the thing this hackathon is scored on. The cost of being wrong is the
agent going dark; the benefit is tidiness. So it waits.

## Why `compose.override.yml` moved here

Docker Compose reads `compose.yml` **and `compose.override.yml`** when both sit
in the working directory. With the legacy override still at the repo root,
`docker compose up --build -d` — the whole install — failed before it started:
the override declares a `hermes` service with no build context and demands
`AGENT_DIR`, a variable only the deployer sets.

```
required variable AGENT_DIR is missing a value: set by agent-mgr from the registry
```

An installer reading that has no way to know it is looking at the wrong file.
So the current path gets the root and the deprecated one gets a directory.

## If the legacy instance ever has to be recreated

`agent-mgr` resolves the override at a fixed path — `<repo>/compose.override.yml`
([`agent_mgr/local.py`](https://github.com/plow-pbc/agent-mgr/blob/main/agent_mgr/local.py))
— so it has to be back at the root for the duration of that command:

```sh
cp legacy/compose.override.yml .
agent-mgr up cfo            # or deploy / restart
rm compose.override.yml
```

**Without it, the container comes up with no skill mounts at all** — and that
failure is silent in the worst way: the agent boots, answers the phone, and is
a generic Hermes with no ledger and no persona. `doctor.py` is what catches it
(`skills 0/8`), and it is why that check exists.

The running container does not need the file: its mounts were resolved when it
was created, and `docker restart hermes-cfo` re-uses them.

## What else at the root is the deprecated deployer's

Left in place, because `agent-mgr` reads them from the repo root by name:

| file | what it is for |
|---|---|
| `agent.env` | the descriptor: which config, which deploy hook, which cron spec |
| `config.yaml` | the home's config for a legacy instance. **The current image ships its own**, and `plow-init` asserts what it owns on every boot |
| `runtime-cron.json` | the four rows `agent-mgr cron-sync` registered. The current contract registers two (the two that wake the model) and supervises the other two as services |
| `runtime/SOUL.md` | the identity as a whole file, bind-mounted over the image's. **Frozen**: the current contract uses `runtime/persona.md`, which the base composes with its own persona at every boot |
| `scripts/deploy-hook.sh` | copied the gates into the home, which the `cfo-schedule` service now does |
| `cfo-shared/scripts/usage_report.sh` | the hourly index report. The current contract reports every five minutes from a supervised service |

`runtime/SOUL.md` and `runtime/persona.md` say the same things and will drift
the day somebody edits one of them. `tests/test_persona.py` fails when the
rules that must never be lost are missing from either.

## Migrating the legacy instance, when the hackathon is over

The ledger is the only thing that matters, and it is one SQLite file:

```sh
# 1. the ledger, out of the legacy home
cp -r ~/.hermes-cfo/cfo /tmp/cfo-ledger-backup

# 2. a line for the new instance, and the image
plow-agents lines                  # the legacy agent holds one; revoke or use another
./install.sh

# 3. the ledger in, before the first message
docker compose cp /tmp/cfo-ledger-backup/ledger.db agent:/var/lib/hermes/cfo/ledger.db
docker compose exec agent chown 10000:10000 /var/lib/hermes/cfo/ledger.db
docker compose restart agent
```

Then `agent-mgr down cfo && agent-mgr unregister cfo`, and this directory goes.
