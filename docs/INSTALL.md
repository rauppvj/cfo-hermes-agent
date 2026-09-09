# Install cfo

**A financial manager you text.** This page is the whole install, in order,
with what each step will ask you for. It takes about ten minutes, and most of
that is Docker pulling an image.

When it is done you will have your own instance: your own container, your own
home directory, and a ledger that is a SQLite file on your Mac. Nothing about
your spending is sent anywhere to be stored — there is no server here and no
account to create.

---

## Before you start

| you need | check it with | if it is missing |
|---|---|---|
| **Docker**, running | `docker info` | [docker.com/get-started](https://docker.com/get-started) — start Docker Desktop before continuing |
| **python3 3.11+** | `python3 -V` | `brew install python@3.12` |
| **git** | `git --version` | `xcode-select --install` |
| **gh**, signed in | `gh auth status` | [cli.github.com](https://cli.github.com), then `gh auth login` |
| **a phone** | — | the handset you text the agent from. It will own this agent permanently |

`gh` is not optional and not about this repo: the deployer fetches the Plow
Chat plugin with it.

Turn Docker Desktop on to start when you sign in (Settings → General). The
agent's container restarts itself, but it cannot restart while the Docker
daemon is down — and an agent that is off reports nothing and answers nothing.

---

## Install

```sh
git clone https://github.com/rauppvj/cfo-hermes-agent.git
cd cfo-hermes-agent
./install.sh
```

That is the command. The script installs the deployer, registers this checkout,
builds and starts the container, and registers the scheduled jobs.

**It stops twice**, both times for something only you can do.

### Stop 1 — activation

It prints a phrase like:

```
Text  Plow Activate: 1234-5678  to  +1 650 ...
```

Text that phrase, from the phone that should own this agent. **The handset that
answers owns it permanently** — this is a one-time step and the installer will
refuse to repeat it, so send it from the right phone.

### Stop 2 — the model credential

It prints a device code and a URL. Open the URL, enter the code, approve. This
is the model provider's own sign-in, not Plow's and not this repo's.

### Re-running is safe

Every step checks whether it is already done and says so instead of doing it
again. If something fails halfway — a network blip, a Docker restart — run
`./install.sh` again and it resumes.

---

## First conversation

Text your agent. It answers in whatever language you write in.

```
you   hi
cfo   ...asks for your city and your currency
```

Tell it where you live. That one answer sets the timezone, which decides which
*day* — and on the 31st, which *month* — an expense belongs to. Then:

```
you   spent 40 on lunch
cfo   Recorded: $40.00 on food. You're at $512.00 this month.

you   can I afford a 2,000 monitor?
cfo   It fits, but the month closes $300.00 in the red at this pace.
```

**Start from your bank statement, not from typing.** Send the file to the chat
as an attachment — CSV, or a PDF where your bank offers no export — and it
imports. Credit-card invoices too; they are a different document and are parsed
as one.

### The two briefs

At 08:00 your local time it tells you where the month is heading. At 22:00 it
tells you about the day, and only when the day gave it something to say. Move
the hour by asking — *"send me the brief at 7"* — or turn it off the same way.

### The panel

The same ledger renders as one page on your own disk:

```
~/.hermes-cfo/cfo/panel/index.html
```

Open it by double click and put it full-screen on a spare monitor or an old
iPad. It redraws on every write to the ledger and every ten minutes otherwise.
No server, no port, nothing fetched from anywhere.

---

## Optional: Plow Latch

[Latch](https://plow.co/latch) lets the agent reach your Mac and take a
statement out of `~/Downloads` itself. **Most people should skip it** — sending
the file to the chat imports identically. Add it later:

```sh
agent-mgr set-latch cfo && agent-mgr deploy cfo
```

---

## Checking it works

```sh
# the container is up
docker ps --filter name=hermes-cfo

# the ledger answers, from the same code the agent calls
docker exec hermes-cfo sh -c 'python3 "$HERMES_HOME"/skills/cfo-shared/scripts/money.py summary'

# what it is reporting to the Agent Index, and whether that is landing
tail -20 ~/.hermes-cfo/logs/agent-index.log
```

That last log is the one to read if the agent looks idle on the index. A report
lands as `200 {'ok': True, ...}`; anything else is in there with the reason.

---

## When something is wrong

**"docker is installed but not running"** — start Docker Desktop and re-run.

**The agent answers but knows nothing about money.** Its skills did not land
where the gateway reads. Ask the container itself:

```sh
docker exec hermes-cfo sh -c 'ls "$HERMES_HOME"/skills | grep cfo'
```

Eight `cfo-*` lines is right. **Nothing is the failure to know about** — the
mounts still succeeded, at a path this image does not read, so the agent boots
and answers and simply is not this agent. `git pull` in this repo and in
`~/services/agent-mgr`, then `agent-mgr deploy cfo`.

**No brief arrived.** The hour is the *owner's* hour, read from the ledger, so
check what it thinks it is:

```sh
docker exec hermes-cfo sh -c 'python3 "$HERMES_HOME"/skills/cfo-shared/scripts/money.py config timezone'
```

**Nothing on the Agent Index.** Read `~/.hermes-cfo/logs/agent-index.log`. It is
the only place an hourly `deliver: local` job leaves a trace.

---

## Turning things off

```sh
# stop reporting usage to the Agent Index -- the agent works the same
docker exec hermes-cfo sh -c 'python3 "$HERMES_HOME"/skills/cfo-shared/scripts/money.py \
    config usage_reporting off'

# stop the agent
agent-mgr down cfo

# remove it, keeping the ledger (it is ~/.hermes-cfo, delete that yourself)
agent-mgr unregister cfo
```

---

## What this sends, and what it does not

The ledger is a file in `~/.hermes-cfo`, written by your instance and by
nothing else. It is not uploaded, backed up, or synced.

What *is* sent is one thing: an hourly count of **how many tokens this agent
used**, per day per model, to the [Agent
Index](https://aiworthusing.com/agent-index). No prompts, no messages, no
transactions, no totals, no file paths. Turn it off with the command above.

The language model still sees what you text it, the way any agent does. That is
the honest boundary, and it is worth knowing which side of it your data is on.
