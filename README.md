# cfo

**A financial manager you text.** Log what you spend in the words you'd use
with a person, ask where the month is heading, and find out what a purchase
actually costs you before you make it — over iMessage, with the ledger living
as a file on your own machine.

A [Hermes](https://howto.plow.co/hermes) agent, texted through
[Plow Chat](https://plow.co) and installed with one command. Optionally drives
your own Mac through [Plow Latch](https://plow.co/latch). Deployed with
[`plow-pbc/agent-mgr`](https://github.com/plow-pbc/agent-mgr).

```
you   spent 40 on lunch
cfo   Recorded: $40.00 on food. You're at $512.00 this month.

you   can I afford a 2,000 monitor?
cfo   It fits, but the month closes $300.00 in the red at this pace.
      Split in 3 it fits comfortably — $666.68 now and two of $666.66.

08:00 Morning. Yesterday $87.00. At this pace September closes at
      $3,240.00, $400.00 over plan. Food has already passed the whole
      of August.

22:00 $132.40 so far today. The condo fee is due tomorrow, $420.00.
```

Both at those hours **where you live** — and the evening one only when the day
gave it something to say.

It answers in the language you text it in, in the currency you set. The same
agent, from the same install:

```
you   gastei 40 no almoço
cfo   Anotado: R$ 40,00 em alimentação. Você está em R$ 512,00 esse mês.
```

> [!IMPORTANT]
> **This repo is code only.** Nothing under this tree may carry a credential,
> a chat id, or anybody's transactions. The ledger is a SQLite file in the
> instance's own home on the host (`~/.hermes-<name>`, mounted at
> `/opt/data`), written by the owner's instance and by nothing else. There is
> no server, no account, and no sign-up: your spending is not sent anywhere
> to be stored. The language model still sees what you text it, the way any
> agent does — that is the honest boundary, and it is worth knowing which
> side of it your data is on.

**One repo, one instance per person.** This is not one person's agent that
someone else copies; each person who runs it gets their own instance, their
own home, and their own ledger, from this one checkout.

## The one rule the whole thing rests on

**The model reads, the code calculates.**

Hermes turns *"spent 40 on lunch"* into a `money add` call and reads the
fields that come back. It never does the arithmetic. A language model that
sums a column of numbers will one day sum it wrong and say so with exactly
the same confidence — and in a budget, a number that is quietly wrong is
worse than no number at all.

So every figure this agent says traces to
[`cfo-shared/scripts/money.py`](cfo-shared/scripts/money.py): amounts are
integer cents, never floats, and each subcommand prints JSON with both the
raw value and a formatted twin. You can check any answer it gives you by
running the same command yourself.

The rule has a second half that matters as much: **when the arithmetic is
standing on too little, the command says so rather than answering anyway.**
`project` returns a `basis` — how many days of spending it has, whether any
income is known, how much of the month being projected has actually elapsed —
and `basis.usable` is false when the answer would be real arithmetic on
nothing. A month one day old is not a pace: one trip to the shop times thirty
is a formatted, sourced, meaningless number, and that is the only kind a
person cannot catch.

## Try it in one minute

The engine is a plain Python CLI with no dependencies — you can drive the
ledger before setting up any of the agent machinery:

```sh
git clone https://github.com/rauppvj/cfo-hermes-agent.git
cd cfo-hermes-agent
export CFO_DATA=/tmp/cfo-try

python3 cfo-shared/scripts/money.py config timezone America/New_York
python3 cfo-shared/scripts/money.py config currency USD
python3 cfo-shared/scripts/seed_demo.py          # 3 months of sample data

python3 cfo-shared/scripts/money.py day             # yesterday, one number
python3 cfo-shared/scripts/money.py summary
python3 cfo-shared/scripts/money.py project
python3 cfo-shared/scripts/money.py simulate "1,200.00" --installments 3

open /tmp/cfo-try/panel/index.html                  # the same month, as a screen
```

The sample data is **deterministic per month**: three completed months seeded
from a fixed RNG, so those numbers are identical on every machine and in the
demo video, plus the current month filled in up to today — because a sample
whose current month is empty cannot answer the first question anyone asks it.
Seeded rows are marked `source: demo`, and `seed_demo.py --reset` removes them
while leaving anything you logged yourself untouched.

## The panel: the second surface

The chat answers when you ask it something. A screen you walk past answers
before you ask — so the same ledger also renders as one page:

    ~/.hermes-cfo/cfo/panel/index.html

Open it by double click, put it full-screen on a spare monitor or an old
iPad, and it shows the month's spend, where it closes at the current pace,
today and yesterday, the categories, and the bills due in the next seven
days. It reloads itself every minute; **every write to the ledger redraws
it**, so a spend texted from the sofa is on the kitchen screen before the
reply arrives.

**No model writes any figure on that page.** `panel.py` renders it from the
same functions `money.py` prints from — which is also why it shows no
projection in the first days of a month: `basis.usable` is false, and a
number nobody is reading is the worst place to guess. And nothing on the page
is fetched from anywhere: no CDN, no font, no script. It is a file on your
disk, opened over `file://`, with no server and no port — the same promise
the ledger makes.

<details>
<summary>Where it comes from, and how to move it</summary>

Redraw it by hand — after editing anything, or just to see it work:

```sh
python3 cfo-shared/scripts/panel.py            # writes $CFO_DATA/panel/index.html
python3 cfo-shared/scripts/panel.py --json     # the same figures, as data
CFO_PANEL=~/Desktop/cfo.html python3 cfo-shared/scripts/panel.py
```

A `cfo-panel` cron row keeps it current when nobody is texting: `no_agent`,
so the scheduler runs the script and never wakes the model — no tokens, no
message, ten minutes apart. That tick is not redundant with the write hook:
at midnight "today" becomes a different day on a page nobody has touched.

The language follows `money.py config language pt|en`, and without that
setting it follows the currency.
</details>

## Usage, and the Agent Index

This agent is published on the [Agent Index](https://aiworthusing.com/agent-index)
as `cfo`, and each install can report **how much it ran** — token counts per
day per model, through
[`plow-pbc/agent-index-client`](https://github.com/plow-pbc/agent-index-client),
one file of standard-library Python that lands in your own home at
`~/.hermes-<name>/scripts/agent_index_client.py`.

**What it does not send: anything else.** No prompts, no messages, no
transactions, no totals, no file paths, no costs. Your ledger is not part of
it and never leaves the machine. The installer asks before signing in, and
skipping it changes nothing about how the agent works.

The credential is yours: your GitHub approves it, it is stored in your home,
and nothing is baked into the repo. Sign in later, or stop:

```sh
C=~/.hermes-cfo/scripts/agent_index_client.py
docker exec -it hermes-cfo env HOME=/opt/data python3 $C --agent cfo --login
docker exec hermes-cfo env HOME=/opt/data python3 $C --agent cfo --dry-run

rm ~/.hermes-cfo/.agent-index/token     # stop reporting, keep the agent
```

Reporting starts at the **first run**: the client records a baseline and
sends the difference from then on, so nothing before it is counted — by
design, so a long-lived session cannot dump weeks of history onto one day.

<details>
<summary>How it is wired, and the two variables that decide whether it works</summary>

An hourly `cfo-usage` cron row runs
[`usage_report.sh`](cfo-shared/scripts/usage_report.sh) with `no_agent` — the
script is the job, so no model wakes up and nothing is delivered to the chat.
The wrapper exists for two environment variables:

- **`HOME=/opt/data`** — the client keeps the credential at
  `~/.agent-index/token` and its collection baseline next to it. The container
  runs with `HOME=/root`, which is the image's own layer, and every
  `agent-mgr deploy` recreates the container: both files would vanish, the
  credential silently.
- **`HERMES_HOME=/opt/data`** — where `state.db` is. A wrong path is not an
  error; it reads as **zero tokens**, which on a public index looks like an
  agent nobody uses rather than one nobody configured.

Publishing the agent itself is a separate, one-time act by whoever owns the
id — `--register` claims it and cannot be undone, so it is not part of any
install:

```sh
python3 agent_index_client.py --register --agent cfo \
  --name "cfo" \
  --blurb "A financial manager you text. Log what you spend in plain language, ask where the month is heading — the ledger stays a file on your own Mac." \
  --repo https://github.com/rauppvj/cfo-hermes-agent \
  --runtime "Hermes / Plow" \
  --builder-name "Vinicius Raupp" --builder-handle @rauppvj \
  --image https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/panel.png
```
</details>

## Start from the statement, not from typing

An empty ledger answers nothing, and nobody types ninety days of history into
a chat. So the way in is the file the bank already gave you: **send it to the
agent as an attachment** and `cfo-import` reads it — or, if Latch is set up,
it takes the file out of `~/Downloads` without being sent anything. Both
handle either document people mean by "my statement".

**Bank statements** — CSV, or a PDF where the bank offers no export, in any
language. The parser is built around what real files do rather than what a
format says: the year printed once in a section header and never on the rows,
two date columns where the second leads the description, non-breaking spaces
inside `R$ 1.800,00`, and every row opening with a transaction type that
buries the merchant.

**Credit-card invoices** — a fatura is a different document and every
assumption a statement parser makes about it is wrong. Rows date themselves
`29 abr` with no year anywhere on the line. A foreign purchase prints three
numbers and the exchange rate is *last*, so reading the amount at the end of
the line — correct on a statement, where the last number is the balance —
imports the rate. And the invoice carries its own settlement of last month,
unsigned, in the middle of the purchases: counted as spending it nearly
doubles the month, and it is already on the bank statement as the payment
leaving the account. It is excluded and named, not silently dropped.

Then the part a keyword rule cannot do. Rules catch the chains and miss
everything local, which in a real statement is most of it, so `uncategorized`
returns the **distinct payees** — a hundred and fifty rows come back as forty
names — and the model classifies them by name, which is the one job here it is
genuinely better at than code. What it names is **kept**: the map is stored and
applied to every later import, so the second statement arrives already sorted
and the owner is asked once rather than every month.

Nothing about the import is arithmetic, which is why the model is allowed near
it at all. Every total it reports still comes from `money.py`.

## Install it

Prerequisites: `docker` running, `python3` 3.11+, `git`, and an authenticated
`gh` (`gh auth login`).

```sh
git clone https://github.com/rauppvj/cfo-hermes-agent.git
cd cfo-hermes-agent
./install.sh
```

It stops twice, both times for something only you can do: texting an
activation code from the phone that will own the agent, and entering a device
code for the model provider. Everything else — installing `agent-mgr`,
registering, deploying, starting the container, registering the brief — it
does.

**Re-run it whenever.** Every step checks whether it is already done and says
so instead of repeating it. `activate` is guarded hardest: it is a one-time
spend that binds the agent permanently to the handset that answers it, so it
never runs twice.

<details>
<summary>What it does, if you would rather run it yourself</summary>

```sh
git clone https://github.com/plow-pbc/agent-mgr.git ~/services/agent-mgr
ln -sf ~/services/agent-mgr/agent-mgr ~/.local/bin/agent-mgr

agent-mgr register cfo /path/to/cfo-hermes-agent
agent-mgr deploy cfo
agent-mgr activate cfo      # prints a code — text it from the owner's phone
agent-mgr up cfo
agent-mgr cron-sync cfo     # registers the hourly brief tick
agent-mgr sign-in cfo       # device-code OAuth for the model credential
```
</details>

**[Plow Latch](https://plow.co/latch) is optional** — the installer offers it
last and most people should skip it. It lets the agent reach the Mac and pick
a statement out of `~/Downloads` itself; without it you send the statement to
the chat as an attachment and the import is identical. Add it later with
`agent-mgr set-latch cfo && agent-mgr deploy cfo`; the deploy is what turns
the declaration on, and with no credential on file it stays off rather than
retrying a connection it cannot make on every boot.

**There is no timezone to set here.** You tell the agent what city you are in,
in the chat, and that is the only place the zone lives:

> The zone decides which **day** — and on the 31st, which **month** — an
> expense belongs to. An expense at 22:00 in São Paulo is 01:00 the next day
> in UTC and 18:00 the same day in Los Angeles. Every row stores both the UTC
> instant and the owner's local day, resolved once at write time against
> `money.py config timezone`.
>
> The brief follows the same setting. The registered job ticks hourly and
> [`brief_gate.py`](cfo-shared/scripts/brief_gate.py) answers every tick that
> is not a brief hour *for this owner* with `{"wakeAgent": false}` — which the
> scheduler reads as "skip the agent entirely": no model run, no delivery, no
> cost. A cron expression could not do this. It fires in the container's zone,
> which agent-mgr defaults to `America/Los_Angeles` for the whole fleet, so a
> `0 8 * * *` brief reaches Tokyo at midnight and nothing anywhere reports it.
>
> Change the hour by asking: *"send me the brief at 7"* → `money.py config
> brief_hour 7`, or `off` to stop it. The setter refuses anything that is not
> an hour, because a schedule that is quietly broken and one that is working
> look exactly alike.

`activate` is a **one-time spend and the handset that texts the code owns the
agent permanently** — send it from the phone that should own it.

## Skills

| skill | what it does |
|---|---|
| [`cfo-log`](cfo-log/SKILL.md) | records a spend or income from plain language |
| [`cfo-ask`](cfo-ask/SKILL.md) | answers questions about the month, a category, a comparison |
| [`cfo-simulate`](cfo-simulate/SKILL.md) | what a purchase does to the month, upfront or split |
| [`cfo-brief`](cfo-brief/SKILL.md) | the morning and evening briefs — the only times it speaks first |
| [`cfo-setup`](cfo-setup/SKILL.md) | first run: timezone, currency, fixed lines, sample data |
| [`cfo-import`](cfo-import/SKILL.md) | reads a bank statement or card invoice off the owner's Mac |
| [`cfo-panel`](cfo-panel/SKILL.md) | the wall panel — where it is, how to refresh it, how to open it |

## Tests

```sh
python3 -m pytest tests/ -q
```

127 of them, and the ones that earn their place are the boundary tests: an
amount read in the wrong locale (`R$ 1.234,56` vs `1,234.56`), a day resolved
in the wrong zone, a merchant name matched inside a longer word (`Raia` in
`PRAIA GRANDE`), four identical bus fares on one afternoon collapsed into one
by a deduplicator, a brief hour that opens at 08:00 in Tokyo rather than in
the container's Los Angeles. Every one of those fails *silently* — it produces
a number, or a message at the wrong hour, and nobody notices until the month
closes.

A test here is only trusted once it has been run against the code from
**before** the fix and seen to fail. Two early drafts of one passed against
malformed fixtures and proved nothing, which is why the assertions pin real
figures rather than shapes.

## Currency and locale

`money.py` reads `40`, `40.50`, `40,50`, `R$ 1.234,56` and `1,234.56`
correctly regardless of the configured currency; the setting only decides how
figures are printed back. `BRL`, `USD`, `EUR` and `GBP` are formatted
natively. Nothing about this agent is specific to one country — there is no
bank integration to be missing.

## License

MIT.
