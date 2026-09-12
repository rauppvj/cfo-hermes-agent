# Install cfo

**A financial manager you text.** This page is the whole install, in order,
with what each step will ask you for. It takes about ten minutes, and most of
that is Docker building the image.

When it is done you will have your own instance: your own container, your own
line, and a ledger that is a SQLite file in a volume on your Mac. Nothing
about your spending is sent anywhere to be stored — there is no server here
and no account to create beyond the Plow one your phone already texts.

---

## Before you start

| you need | check it with | if it is missing |
|---|---|---|
| **Docker**, running | `docker info` | [docker.com/get-started](https://docker.com/get-started) — start Docker Desktop before continuing |
| **Docker Compose v2** | `docker compose version` | ships with Docker Desktop; update it |
| **python3** | `python3 -V` | `brew install python@3.12` — the Plow CLI is standard library only |
| **git** | `git --version` | `xcode-select --install` |
| **a Plow account** | the phone you text Plow from | [plow.co](https://plow.co) |

There is **no GitHub account** in that list, and no model provider sign-in.
Both used to be required — the deprecated deployer fetched the chat plugin
with `gh`, and inference needed its own device-code flow. The current contract
has neither: the plugin is in the base image and inference comes with the Plow
credential.

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

That is the command. The script gets the Plow CLI, logs this machine in, mints
this agent's credential against a free line, builds the image, starts it,
waits for it to say who it is, and ends by checking its own work.

**It stops for three things**, each one something only you can decide.

### Stop 1 — logging this machine in

It prints a phrase like:

```
Text  Plow Activate: 1234-5678  to  +1 650 ...
```

Text that from the phone on your Plow account. This logs **this machine** in —
it writes an account token to `~/.config/plow/token` and creates nothing.

### Stop 2 — the line

`cfo` answers on a Plow line, and a line can hold only one agent: two agents
on one line both reply to the same chat and you cannot tell which answered. So
the script lists your lines and their status:

```
LINE        NAME        NUMBER          STATUS
ln_a1b2c3   assistant   +1 650 ...      free
```

If nothing is `free`, it offers to ask Plow for another line. That is a real
change to your account, so it asks first.

### Stop 3 — minting

Minting creates this agent in Plow and writes its credential to
`./plow-credentials`. **Keep that file.** It is this agent's identity, it is
not in git, and `plow-agents rotate` is how you replace it.

### Re-running is safe

Every step checks whether it is already done and says so instead of doing it
again. If something fails halfway — a network blip, a Docker restart — run
`./install.sh` again and it resumes. It will not mint a second credential over
one that exists.

### The last thing it prints

```
==> Checking the install
  ✓ contract             plow-agents (this repo's image)
  ✓ home                 HERMES_HOME=/var/lib/hermes
  ✓ skills               8/8 under /var/lib/hermes/skills
  ✓ soul                 the cfo persona is in place
  ✓ ledger               /var/lib/hermes/cfo/ledger.db · schema 4 · 0 rows
  ✓ cron                 cfo-brief last never, cfo-notify last never
  ✓ services             agent-index up, cfo-panel up, cfo-schedule up
  ✓ panel reachable      /srv/cfo/panel is mounted from the host
  ✓ index id             cfo
  ✓ index registration   registered · install 3f2a...

  all good -- this is cfo. Text it to set it up.
```

That is `doctor.py`, and it exists because **every failure this install can
have is silent**: skills that land where the gateway does not read, a usage
report failing into a log nobody opens, a panel redrawn where nobody can open
it, a stale schedule. A `✗` line says what is wrong and what fixes it.

Run it any time:

```sh
docker compose exec agent /opt/hermes/.venv/bin/python3 \
    /var/lib/hermes/skills/cfo-shared/scripts/doctor.py
```

---

## First conversation

Text your agent at the number the install printed. It answers in whatever
language you write in.

```
you   hi
cfo   ...asks for your city and your currency
```

Tell it where you live. That one answer sets the timezone, which decides which
*day* — and on the 31st, which *month* — an expense belongs to. Then:

```
you   spent 40 on lunch
cfo   Recorded: $40.00 on food. You're at $512.00 this month.

you   I have 1,312.40 in the account
cfo   Noted. $1,312.40 in the account, $60.00 open on the card.

you   how much do I have?
cfo   $1,272.40 in the account — $852.40 after Friday's rent.
      $60.00 on the card, unpaid.

you   can I afford a 2,000 monitor?
cfo   It fits, but the month closes $300.00 in the red at this pace.
```

**Start from your bank statement, not from typing.** Send the file to the chat
as an attachment — CSV, or a PDF where your bank offers no export — and it
imports. Credit-card invoices too; they are a different document and are
parsed as one.

**Stop typing purchases at all.** A shortcut on the iPhone catches every card
tap at a terminal, and a watcher on this Mac catches the notifications your
bank sends for everything else — the physical card, débito, Pix, a card saved
in an app. [`AUTOPILOT.md`](AUTOPILOT.md).

### The two briefs

At 08:00 your local time it tells you what yesterday cost, what is in the
account and where the month is heading. At 22:00 it tells you about the day,
and only when the day gave it something to say. Move the hour by asking —
*"send me the brief at 7"* — or turn it off the same way.

### The panel

The same ledger renders as one page on your own disk:

```sh
open panel/index.html
```

Double click it and put it full-screen on a spare monitor or an old iPad. It
redraws every ten minutes and on every write to the ledger. No server, no
port, nothing fetched from anywhere — and no model, either: the page is a
SQLite read, so it costs nothing to keep current.

---

## Optional: Plow Latch

[Latch](https://plow.co/latch) lets the agent reach your Mac — it can then
take a statement out of `~/Downloads` itself. **Most people should skip it**:
sending the file to the chat imports identically. If you want it, pair your
Mac in Plow; the credential `mint` wrote already carries the permission to
reach it, so there is no second step here. (Under the deprecated deployer this
was `agent-mgr set-latch`, which no longer exists.)

---

## When something is wrong

**"docker is installed but not running"** — start Docker Desktop and re-run.

**It parked instead of starting.** `plow-init` asks Plow who holds this
credential and refuses to boot on an answer it does not understand, rather
than coming up as whoever the home volume belonged to last. It says why:

```sh
docker compose logs agent | grep -i park
```

`no credential at /var/lib/plow/credentials` means `mint` has not run.
`cannot tell which chat is home` means the line has more than one active chat
on it, or none.

**`plow-credentials` is a directory.** `docker compose up` ran before the
credential existed, so Docker created the mount target. Fix it:

```sh
docker compose down -v && rmdir plow-credentials
```

...then re-run `./install.sh`. Note `down -v` deletes the home volume, which
on an install this early is empty anyway.

**The agent answers but knows nothing about money.** Its skills did not reach
the home. The doctor's `skills` line says so; or ask the container:

```sh
docker compose exec agent ls /var/lib/hermes/skills | grep cfo
```

Eight `cfo-*` lines is right. Anything less is the failure to know about — the
agent boots, answers, and simply is not this agent. `docker compose up --build -d`
rebuilds and the runtime re-seeds what is missing.

**No brief arrived.** The hour is the *owner's* hour, read from the ledger, so
check what it thinks it is — the doctor's `brief hours` and `brief last
opened` lines. If `cron` shows nothing registered, the `cfo-schedule` service
says why:

```sh
docker compose logs agent | grep cfo-schedule
```

**The panel is not updating.** `doctor.py` has two lines about it: `panel`
says when it was last written, `panel reachable` says whether the directory it
is written into is the one mounted from this Mac. A page redrawn inside the
container only is the one failure that looks perfectly healthy from in there.

**Nothing on the Agent Index.** The reporter is a supervised service, so its
account of itself is the container's log:

```sh
docker compose logs agent | grep agent-index
```

---

## Turning things off

```sh
docker compose down          # stop it, keep the ledger
docker compose up -d         # start it again
docker compose down -v       # stop it and DELETE the ledger volume
plow-agents revoke           # retire the agent in Plow and free its line
```

Your data leaves with you: *"send me my spending"* in the chat delivers a CSV
of the ledger into the conversation, any time.

To stop reporting usage to the Agent Index, comment the `agent-index` service
out of `image/s6-overlay/s6-rc.d/user/contents.d/` and rebuild — there is no
runtime switch on purpose, because a switch is a second place that can
disagree with the image about whether this install reports.

---

## What this sends, and what it does not

The ledger is a file in this instance's own volume, written by your instance
and by nothing else. It is not uploaded, backed up, or synced.

What *is* sent is one thing: a five-minute count of **how many tokens this
agent used**, per day per model, to the [Agent
Index](https://aiworthusing.com/agent-index), plus one random id for this
install so two installs are not added together. No prompts, no messages, no
transactions, no totals, no file paths. The client is one readable file of
standard-library Python, pinned by commit and checksum at build time:
`/opt/plow/agent-index-client.py`.

The language model still sees what you text it, the way any agent does. That
is the honest boundary, and it is worth knowing which side of it your data is
on.
