# cfo

**A financial manager you text.** Log what you spend in the words you'd use
with a person, ask where the month is heading, and find out what a purchase
actually costs you before you make it — over iMessage, with the ledger living
as a file on your own machine.

A [Hermes](https://howto.plow.co/hermes) agent, texted through
[Plow Chat](https://plow.co) and running your own Mac through
[Plow Latch](https://plow.co/latch). Deployed with
[`plow-pbc/agent-mgr`](https://github.com/plow-pbc/agent-mgr).

```
you   gastei 40 no almoço
cfo   Anotado: R$ 40,00 em alimentação. Você está em R$ 512,00 esse mês.

you   posso comprar um monitor de 2 mil?
cfo   Cabe, mas o mês fecha em R$ 300,00 no vermelho no ritmo atual.
      Em 3x cabe folgado — R$ 666,68 agora e mais duas de R$ 666,66.

08:00 Bom dia. Ontem R$ 87,00. No ritmo atual setembro fecha em
      R$ 3.240,00, R$ 400,00 acima do previsto. Alimentação já passou
      o mês inteiro de agosto.
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

Hermes turns *"gastei 40 no almoço"* into a `money add` call and reads the
fields that come back. It never does the arithmetic. A language model that
sums a column of numbers will one day sum it wrong and say so with exactly
the same confidence — and in a budget, a number that is quietly wrong is
worse than no number at all.

So every figure this agent says traces to
[`cfo-shared/scripts/money.py`](cfo-shared/scripts/money.py): amounts are
integer cents, never floats, and each subcommand prints JSON with both the
raw value and a formatted twin. You can check any answer it gives you by
running the same command yourself.

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

python3 cfo-shared/scripts/money.py summary
python3 cfo-shared/scripts/money.py project
python3 cfo-shared/scripts/money.py simulate "1,200.00" --installments 3
```

The sample data is **deterministic per month**: three completed months seeded
from a fixed RNG, so those numbers are identical on every machine and in the
demo video, plus the current month filled in up to today — because a sample
whose current month is empty cannot answer the first question anyone asks it.
Seeded rows are marked `source: demo`, and `seed_demo.py --reset` removes them
while leaving anything you logged yourself untouched.

## Deploy it as an agent

Prerequisites: `python3` 3.11+, `docker`, an authenticated `gh`, and
[Plow Latch](https://plow.co/latch) on the Mac the agent should drive.

```sh
git clone https://github.com/plow-pbc/agent-mgr.git ~/services/agent-mgr
ln -sf ~/services/agent-mgr/agent-mgr ~/.local/bin/agent-mgr

agent-mgr register cfo /path/to/cfo-hermes-agent
agent-mgr deploy cfo
```

**Then set your timezone before starting it** — `agent-mgr resolve cfo` prints
the home; put `AGENT_TZ=America/Sao_Paulo` in the `.env` there:

> This is not cosmetic. agent-mgr's fleet default is `America/Los_Angeles`,
> which belongs to nobody. An expense at 22:00 in São Paulo is 01:00 the next
> day in UTC and 18:00 the same day in Los Angeles — and on the last day of a
> month, the wrong zone files it in the wrong month and closes two months
> wrong. Every row stores both the UTC instant and the owner's local day,
> resolved once at write time.

```sh
agent-mgr activate cfo      # prints a code — text it from the owner's phone
agent-mgr up cfo
agent-mgr cron-sync cfo     # registers the 08:00 brief
agent-mgr sign-in cfo       # device-code OAuth for the model credential
agent-mgr set-latch cfo     # DOMO_DEVICE_UID then DOMO_MCP_TOKEN, on stdin
agent-mgr check-latch cfo
```

`activate` is a **one-time spend and the handset that texts the code owns the
agent permanently** — send it from the phone that should own it.

## Skills

| skill | what it does |
|---|---|
| [`cfo-log`](cfo-log/SKILL.md) | records a spend or income from plain language |
| [`cfo-ask`](cfo-ask/SKILL.md) | answers questions about the month, a category, a comparison |
| [`cfo-simulate`](cfo-simulate/SKILL.md) | what a purchase does to the month, upfront or split |
| [`cfo-brief`](cfo-brief/SKILL.md) | the 08:00 brief — the only time it speaks first |
| [`cfo-setup`](cfo-setup/SKILL.md) | first run: timezone, currency, fixed lines, sample data |

## Tests

```sh
python3 -m pytest tests/ -q
```

The ones that earn their place are the boundary tests: an amount read in the
wrong locale (`R$ 1.234,56` vs `1,234.56`) and a day resolved in the wrong
zone both fail *silently* — they produce a number, just not the right one, and
nobody notices until the month closes.

## Currency and locale

`money.py` reads `40`, `40.50`, `40,50`, `R$ 1.234,56` and `1,234.56`
correctly regardless of the configured currency; the setting only decides how
figures are printed back. `BRL`, `USD`, `EUR` and `GBP` are formatted
natively. Nothing about this agent is specific to one country — there is no
bank integration to be missing.

## License

MIT.
