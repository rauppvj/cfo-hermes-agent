---
name: cfo-setup
description: Set up a new owner's ledger on first contact — currency, timezone, income and fixed monthly costs — and optionally load three months of sample data so the agent has something to talk about immediately. Use on the very first message from someone whose ledger is empty, when the owner asks to change their currency, timezone, salary or recurring bills, or when they ask for sample data or to clear it.
---

# First run

Someone just texted this agent for the first time. What happens in the next
two messages decides whether they keep it.

**Do not interview them.** A form is what every other budgeting app opens
with, and it is why they are abandoned. Get the two things you cannot work
around, then let them use it.

## The two that matter

```sh
S=/opt/data/skills/cfo-shared/scripts
python3 $S/money.py config timezone America/Sao_Paulo
python3 $S/money.py config currency BRL
```

- **Timezone** decides which day — and on the 31st, which *month* — a
  transaction belongs to. The container's clock is the fleet default and
  belongs to nobody, so an unset zone silently files late-evening spending on
  the wrong day. Ask for their city if you cannot infer it.
- **Currency** is display only; the engine reads both `1.234,56` and
  `1,234.56` regardless. `BRL` `USD` `EUR` `GBP` are formatted natively.

Anything else can wait. Do not ask for a salary before they have logged
anything — the first useful exchange is "gastei 40 no almoço" / "anotado".

## Fixed lines, when they come up

Rent, salary, internet — the ones that land every month. Add them as they are
mentioned rather than collecting them up front:

```sh
python3 $S/money.py fixed add "aluguel" "1.800,00" --kind expense --day 5
python3 $S/money.py fixed add "salario" "7000" --kind income --day 5
```

These are what let `project` and `simulate` mean anything: without them a
projection is variable spending against no income, and every purchase looks
unaffordable.

## Sample data

A ledger with nothing in it cannot answer a question, so someone trying the
agent out learns nothing from it. Offer the sample once:

```sh
python3 $S/seed_demo.py            # three months, deterministic
python3 $S/seed_demo.py --reset    # removes ONLY demo rows
```

The seed is fixed, so these numbers are the same on every machine. Say plainly
that it is sample data, and that `--reset` clears it without touching anything
they logged themselves.

## Rules

- **Never invent a timezone or currency.** Ask. Getting it wrong is invisible
  until a month closes wrong.
- One question per message. Two questions in one text is a form.
- Confirm what you set, in one line, and then get out of the way.
