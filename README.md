# cfo

**A financial manager you text.** Log what you spend in the words you'd use
with a person — or let your phone do it — ask what is in the account and
where the month is heading, and find out what a purchase actually costs you
before you make it. Over iMessage, with the ledger living as a file on your
own machine.

A [Hermes](https://howto.plow.co/hermes) agent, texted through
[Plow Chat](https://plow.co) and installed with one command. Optionally drives
your own Mac through [Plow Latch](https://plow.co/latch). This repo **is** the
image: it builds `FROM` the
[Plow base](https://github.com/plow-pbc/plow-hermes-agent) and boots with a
credential from
[`plow-agents`](https://github.com/plow-pbc/plow-agents) — `mint`, then
`docker compose up`. No deployer, no GitHub account, no registry.

```
you   spent 40 on lunch
cfo   Recorded: $40.00 on food. You're at $512.00 this month.

💳    $14.90 · Corner Bakery · Chase          ← your phone sent this, not you
cfo   ✓ $14.90 · Corner Bakery · food · $526.90 this month.

you   how much do I have?
cfo   $1,312.40 in the account — $892.40 after Friday's rent.
      $1,519.81 on the card, unpaid.

you   Saturday I paid the car, 118.60, and Sunday the market, 64.35
cfo   Recorded on Saturday: $118.60, transport. On Sunday: $64.35, groceries.

you   can I afford a 2,000 monitor?
cfo   It fits, but the month closes $300.00 in the red at this pace.
      Split in 3 it fits comfortably — $666.68 now and two of $666.66.

08:00 Morning. Yesterday $87.00. $1,312.40 in the account. At this pace
      September closes at $3,240.00, $400.00 over plan. Food is at 85% of
      its budget.

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
> a chat id, or anybody's transactions. The ledger is a SQLite file in this
> instance's own Docker volume (`/var/lib/hermes/cfo/ledger.db`), written by
> the owner's instance and by nothing else. There is no server, no account, and no sign-up: your spending is not
> sent anywhere to be stored. The language model still sees what you text it,
> the way any agent does — that is the honest boundary, and it is worth knowing
> which side of it your data is on.

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
person cannot catch. `balance` makes the same refusal: with no reading from
the owner it says what to ask for, because income minus expenses since the
ledger began is not what is in anyone's account.

## Two questions people ask as one

*"How much did I spend?"* and *"how much do I have?"* are different questions,
and most tools answer the second with the first. The engine keeps them apart:

- **Spending** is every purchase, by category, whether it went on a card or
  not. That is `summary`, `day`, `week`, `project`.
- **The account** is the last balance you read off your bank — *"I have
  1,312.40"* is a reading, not a transaction — kept current from that instant
  with what actually left: debit spending and card payments. A card purchase
  is spending today and leaves the account only when the invoice is paid;
  paying the invoice leaves the account and is not spending. That is
  `balance`, with `card_open` for what the next invoice already holds.

The first real evening of use produced both mistakes at once: card purchases
subtracted from a "balance" that was really a net, and a paid invoice counted
as spending on top of the purchases it settled. Both are fields now.

## It fills itself in

The chat is the slow part of a money agent, so most of the month should
arrive without typing:

- **Every card tap, every purchase the bank texts about, and every one it
  pushes.** Two signed iPhone shortcuts ship in the repo (install by link or
  QR, set the recipient, create the trigger in one step) for Wallet taps and
  the bank's SMS; and a one-minute watcher on the Mac reads the iPhone's
  mirrored push notifications — the channel that catches the physical card,
  débito, Pix, and the banks that never send SMS. The bank's own text is read
  by code, never guessed; a purchase that arrives on two channels is recorded
  once, and the bank's "débito" corrects the tap's "crédito".
  [`docs/AUTOPILOT.md`](docs/AUTOPILOT.md).
- **A photo of the receipt.** Send the image; the agent reads the total, the
  merchant and the date, and confirms the one line it is about to write.
- **The statement, at month end.** Send the bank's CSV or PDF as an
  attachment — or the card invoice — and ninety days import at once,
  deduplicated against what was already logged. See below.
- **Catching up in one message.** *"Saturday I paid the car, Sunday the
  market, Monday 1,240"* — each row lands on the day you named, not on the
  day you typed it. That is `add --on saturday`, and it exists because one
  evening of catching up once landed four days on one date and the next
  morning's brief opened with a "yesterday" four times too big.

And two things arrive without being asked for: the morning brief, and the
evening one when the day gave it something to say — including, once, a
request for a fresh balance reading when the last one is getting old.

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
python3 cfo-shared/scripts/money.py balance         # in the account, on the card
python3 cfo-shared/scripts/money.py summary
python3 cfo-shared/scripts/money.py budget          # each ceiling, who is near it
python3 cfo-shared/scripts/money.py project
python3 cfo-shared/scripts/money.py simulate "1,200.00" --installments 3

open /tmp/cfo-try/panel/index.html                  # the same month, as a screen
```

The sample data is **deterministic per month**: three completed months seeded
from a fixed RNG, so those numbers are identical on every machine and in the
demo video, plus the current month filled in up to today — because a sample
whose current month is empty cannot answer the first question anyone asks it.
It puts shopping, leisure and subscriptions on a card and pays the invoice on
the 10th, sets three budgets and an opening balance, so every surface has
something to show. Seeded rows are marked `source: demo`, and
`seed_demo.py --reset` removes them while leaving anything you logged yourself
untouched.

## The panel: the second surface

The chat answers when you ask it something. A screen you walk past answers
before you ask — so the same ledger also renders as one page, on your own
disk, in the checkout you installed from:

    open panel/index.html

Open it by double click, put it full-screen on a spare monitor or an old
iPad, and it shows the month's spend, where it closes at the current pace,
today and yesterday, what is in the account and what is on the card, the
categories — each against its budget when it has one — and the bills due in
the next seven days. It reloads itself every minute; **every write to the
ledger redraws it**, so a spend texted from the sofa is on the kitchen screen
before the reply arrives.

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

The [`cfo-panel`](image/s6-overlay/s6-rc.d/cfo-panel/run) service keeps it
current when nobody is texting — ten minutes apart, no model, no tokens, no
message. That tick is not redundant with the write hook: at midnight "today"
becomes a different day on a page nobody has touched.

**It writes to a directory mounted from your Mac** (`./panel`), and that is
not a detail. The home is a Docker volume, so a page written beside the ledger
would be redrawn every ten minutes somewhere nobody can open it — the one
failure that looks perfectly healthy from inside the container. `doctor.py`
checks the mount, not just the file.

The language follows `money.py config language pt|en`, which the agent stores
from your first message; without that setting it follows the currency.
</details>

## Usage, and the Agent Index

This agent is published on the [Agent Index](https://aiworthusing.com/agent-index)
as `cfo`, and each install reports **how much it ran** — token counts per day
per model, through
[`plow-pbc/agent-index-client`](https://github.com/plow-pbc/agent-index-client),
one file of standard-library Python, fetched at build time from the commit
[`vendor/client.pin`](vendor/client.pin) names and refused unless its checksum
matches. It lives at `/opt/plow/agent-index-client.py`, root-owned and outside
every home: what a supervisor runs unattended must not be a file a turn can
rewrite.

**What it does not send: anything else.** No prompts, no messages, no
transactions, no totals, no file paths, no costs. Your ledger is not part of
it and never leaves the machine.

**There is nothing to sign in to.** Identity starts as this container's own
Plow token: registering trades it, via Plow, for an Index key this install
keeps, and every report after that carries the key alone. No second account,
no device flow. To turn it off, build an image without the reporter — comment `agent-index`
out of `image/s6-overlay/s6-rc.d/user/contents.d/` and rebuild. There is no
runtime switch, on purpose: a flag would be a second place that can disagree
with the image about whether this install reports, and the Dockerfile has
already answered the question.

Reporting starts at the **first run**: the client records a baseline and sends
the difference from then on, so nothing before it is counted — by design, so a
long-lived session cannot dump weeks of history onto one day.

<details>
<summary>How it is wired, and the four things that decide whether it works</summary>

The [`agent-index`](image/s6-overlay/s6-rc.d/agent-index/run) service, every
five minutes, beside the gateway. Not a cron row: a cron row lives in
`$HERMES_HOME/cron/jobs.json`, which nothing replays after a rebuild, so an
install could stop reporting on a machine where everything else was fine.
Supervised, its account of itself is the container's own log
(`docker compose logs agent | grep agent-index`).

- **`AGENT_ID`**, from `environment:` in [`compose.yml`](compose.yml). The
  credential says who the *owner* is; nothing in it says which agent on the
  index this is. Without it the reporter **stands down rather than guessing a
  name** — deliberately, because guessing would publish one agent's usage on
  another's page.
- **the Index key**, in `$HERMES_HOME/.agent-index.json` **on the volume** —
  the only credential a report needs, and the file that says *which install*
  this is. On the volume so a recreated container is still the same install
  rather than a second one splitting its own numbers.
- **`PLOW_AGENT_TOKEN`**, for the one registration and nothing else. It is
  exchanged at Plow for a short-lived assertion; the index mints a
  report-only key against that. Reports carry the key alone.
- **`HERMES_HOME`** — where `state.db` is. Fixed by the image at
  `/var/lib/hermes`, never a second knob: a path that holds no `state.db` is a
  **collector failure**, and the client stops the whole report rather than
  overwrite a correct total with a smaller one.

**Registering is not this repo's job on an install that is not the
publisher's.** `--register` against an agent id somebody else published
answers 409 — the page stays theirs — and the client mints this install's
report key anyway. That behaviour arrived in
[`agent-index-client#11`](https://github.com/plow-pbc/agent-index-client/pull/11)
on 2026-09-11, and before it the client **exited on the 409**: every install
but the publisher's own reported nothing, forever, on the single number this
hackathon ranks by. That is why `vendor/client.pin` names a commit newer than
the one the reference agent pins.

Publishing the agent itself is a one-time act by whoever owns the id, and needs
no account of any kind — the container's Plow token is the proof, and the same
call mints the key every later report uses:

```sh
docker compose exec -e HOME=/var/lib/hermes agent \
  /opt/hermes/.venv/bin/python3 /opt/plow/agent-index-client.py \
  --agent cfo --register \
  --name "cfo" \
  --blurb "A financial manager you text. Log what you spend in plain language, ask where the month is heading — the ledger stays a file on your own Mac." \
  --repo https://github.com/rauppvj/cfo-hermes-agent \
  --runtime "Hermes / Plow" \
  --install-url https://github.com/rauppvj/cfo-hermes-agent/blob/main/docs/INSTALL.md \
  --image https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/chat-and-panel.png
```

**`--install-url` is not optional in practice.** A community agent has no cloud
deploy path, so the index turns the install button on its page into that link —
and with none set, the page shows its owner a note asking for one where every
visitor expects the way in. An agent ranked on installs with nothing to click
is the whole ranking, lost to a missing flag.

**Your name on the page does not come from here.** There is no `--builder-name`
any more: the index resolves the builder from the **Plow profile** behind that
token, per request, and renders "an anonymous builder" when Plow holds no name.
A name Plow holds shows up immediately — no re-registration, no deploy.

Getting Plow to hold one was impossible for five days — writing the profile
needed a scope no owner credential had — and is one `PATCH /v1/auth/profile`
with the container's own token since 2026-09-09. Both halves are in
[`docs/builder-identity.md`](docs/builder-identity.md).

> **The identity model changed under this repo twice on 2026-09-03.** It was a
> GitHub account proven by device flow; then the container's Plow token sent as
> the bearer; and is now an Index key minted from that token. Each change made
> the previous credential a 401 the same day, and the first of them dropped
> every registration made under the old keys. If reporting is silently at zero,
> that class of break is the first thing to check: read the service's log
> above, bump [`vendor/client.pin`](vendor/client.pin) to a current commit of
> the client and rebuild, and register again to mint a current key.
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
buries the merchant. The line where the card was paid is recognised and
recorded as a transfer, not as spending.

**Credit-card invoices** — a fatura is a different document and every
assumption a statement parser makes about it is wrong. Rows date themselves
`29 abr` with no year anywhere on the line. A foreign purchase prints three
numbers and the exchange rate is *last*, so reading the amount at the end of
the line — correct on a statement, where the last number is the balance —
imports the rate. And the invoice carries its own settlement of last month,
unsigned, in the middle of the purchases: counted as spending it nearly
doubles the month, and it is already on the bank statement as the payment
leaving the account. It is excluded and named, not silently dropped. Every
purchase on it lands on the card, so the open invoice is right after an
import too.

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

**Step by step, with what each stop asks you for: [`docs/INSTALL.md`](docs/INSTALL.md).**

Prerequisites: `docker` running with Compose v2, `python3`, `git`, and a Plow
account. **That is the whole list** — and it is the list that matters, because
the Agent Index publishes an install-success rate per agent and this one read
33% under the old one. What left it: an authenticated GitHub CLI (the
deprecated deployer fetched the chat plugin with `gh`), a model-provider
device-code sign-in, a registry row tying one checkout to one instance, and an
`activate` step that minted a credential, DM'd the owner, bound the agent
permanently to whichever handset answered, and could never be repeated.

```sh
git clone https://github.com/rauppvj/cfo-hermes-agent.git
cd cfo-hermes-agent
./install.sh
```

It stops three times, each for something only you can decide: texting a code
to log this machine in, picking the line the agent will answer on, and
approving the mint that creates it. Everything else — the Plow CLI, the build,
the boot, the schedule — it does, and it ends by checking its own work:

```sh
docker compose exec agent /opt/hermes/.venv/bin/python3 \
    /var/lib/hermes/skills/cfo-shared/scripts/doctor.py
```

[`doctor.py`](cfo-shared/scripts/doctor.py) exists because every failure this
install can have is silent: skills that land where the gateway does not read, a
usage report failing where nobody looks, a stale schedule, a panel redrawn
inside the container where nobody can open it, an identity that is the base's
and not this agent's. One line per check, and what fixes it. It knows which
contract it is running under, so no `✗` line sends you to a command that does
not apply to your install.

**Re-run it whenever.** Every step checks whether it is already done and says
so instead of repeating it. Minting is guarded hardest: a credential that
already exists is never replaced by a second one, because two agents on one
line both answer the same chat and the owner cannot tell which replied.

<details>
<summary>What it does, if you would rather run it yourself</summary>

```sh
git clone https://github.com/plow-pbc/plow-agents.git ~/.local/share/plow-agents
export PATH="$HOME/.local/share/plow-agents/bin:$PATH"

plow-agents login              # prints a code — text it from your Plow phone
plow-agents lines              # pick one whose STATUS is `free`
plow-agents mint ln_xxx        # writes ./plow-credentials
docker compose up --build -d
```

Three commands and a build. The two supervised services and the two cron rows
register themselves on the first boot — nothing here schedules anything by
hand, because a schedule that lives in the home volume is one no rebuild
replays.
</details>

**What runs, and where it lives**

| what | how | why not the other way |
|---|---|---|
| the brief, 08:00 and 22:00 **where the owner lives** | `hermes cron` row, hourly, gated by [`brief_gate.py`](cfo-shared/scripts/brief_gate.py) | it has to wake the model. The gate answers 23 ticks a day with `{"wakeAgent": false}` — no model run, no cost — which is how one schedule is correct in every timezone |
| a forwarded bank notification | `hermes cron` row, every 5 min, gated by [`notify_gate.py`](cfo-shared/scripts/notify_gate.py) | same: the reply is a model's sentence. The gate is silent unless the Mac actually left something in the inbox |
| the wall panel | **supervised service**, every 10 min | no model in it at all: a SQLite read and a string of HTML. A cron row would also be lost on a rebuild, and a panel that stops redrawing looks exactly like one that is current |
| the usage report | **supervised service**, every 5 min | it is the leaderboard, and it must survive a rebuild. In the image or the build failed |

**[Plow Latch](https://plow.co/latch) is optional** — most people should skip
it. It lets the agent reach the Mac and pick a statement out of `~/Downloads`
itself; without it you send the statement to the chat as an attachment and the
import is identical. The credential `mint` writes already carries the
permission to reach a paired Mac, so there is no separate step for it any more
(`agent-mgr set-latch` is gone with the deployer).

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
> cost. A cron expression could not do this. It fires in the **container's**
> zone, and the base image sets none — it has no opinion about where a tenant
> lives — so a `0 8 * * *` brief fires at 08:00 UTC, which is 05:00 in São
> Paulo and 17:00 in Tokyo, and nothing anywhere reports it.
> When the gate opens it also names the language the brief is written in —
> the one the owner writes in, stored on first contact — because the brief
> has no message above it to mirror, and one morning that was enough for a
> Portuguese brief to reach someone who had only ever written English.
>
> Change the hour by asking: *"send me the brief at 7"* → `money.py config
> brief_hour 7`, or `off` to stop it. The setter refuses anything that is not
> an hour, because a schedule that is quietly broken and one that is working
> look exactly alike.

A line holds **one** agent. `plow-agents lines` shows which of yours are free;
minting against one that already answers is refused rather than allowed to
produce two agents replying in the same chat.

## Skills

| skill | what it does |
|---|---|
| [`cfo-log`](cfo-log/SKILL.md) | records a spend, an income, a card tap, a receipt photo, a balance reading or a paid invoice, from plain language — on the day it happened |
| [`cfo-ask`](cfo-ask/SKILL.md) | answers what is in the account, what is on the card, what the month, the week, a day or a budget looks like; exports the ledger |
| [`cfo-simulate`](cfo-simulate/SKILL.md) | what a purchase does to the month and to the account, upfront or split |
| [`cfo-brief`](cfo-brief/SKILL.md) | the morning and evening briefs — the only times it speaks first |
| [`cfo-setup`](cfo-setup/SKILL.md) | first run: language, timezone, currency, income, fixed lines, balance; budgets; cards; the autopilot; sample data |
| [`cfo-import`](cfo-import/SKILL.md) | reads a bank statement or card invoice sent to the chat or picked off the Mac |
| [`cfo-panel`](cfo-panel/SKILL.md) | the wall panel — where it is, how to refresh it, how to open it |

## Tests

```sh
python3 -m pytest tests/ -q
```

Nearly two hundred, and the ones that earn their place are the boundary
tests: an amount read in the wrong locale (`R$ 1.234,56` vs `1,234.56`), a
day resolved in the wrong zone, a merchant name matched inside a longer word
(`Raia` in `PRAIA GRANDE`), four identical bus fares on one afternoon
collapsed into one by a deduplicator, a brief hour that opens at 08:00 in
Tokyo rather than in the container's Los Angeles, a Saturday that was typed
on a Tuesday, a paid invoice that must not be spending, a balance reading
and a purchase logged in the same second. Every one of those fails
*silently* — it produces a number, or a message at the wrong hour, and nobody
notices until the month closes.

A test here is only trusted once it has been run against the code from
**before** the fix and seen to fail. Two early drafts of one passed against
malformed fixtures and proved nothing, which is why the assertions pin real
figures rather than shapes.

## Currency and locale

`money.py` reads `40`, `40.50`, `40,50`, `R$ 1.234,56` and `1,234.56`
correctly regardless of the configured currency; the setting only decides how
figures are printed back. `BRL`, `USD`, `EUR` and `GBP` are formatted
natively. Nothing about this agent is specific to one country — there is no
bank integration to be missing, and the one automation it has runs on the
phone, not on a bank.

## License

MIT.
