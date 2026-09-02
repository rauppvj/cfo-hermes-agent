---
name: cfo-setup
description: Walk a new owner through the short setup that makes the rest of the agent work — timezone, currency, what they earn and how often, and their fixed monthly costs — one question at a time, and optionally load three months of sample data. Use on first contact, whenever `money status` reports `ready: false`, when the owner asks to set things up or change their income, salary date, pay frequency, currency or timezone, when they ask what time the brief arrives or want it at a different hour (or stopped), and when they ask for sample data or to clear it.
---

# Setting someone up

Until this is done the agent cannot do its job — and it fails in the worst
possible way. With no income on file, projected income is zero, so *every*
purchase comes back unaffordable, including a coffee, and the wrong answer
sounds exactly as confident as a right one.

So setup is not paperwork. It is the difference between an agent that works
and one that lies.

## Let the ledger tell you what to ask

```sh
python3 /opt/data/skills/cfo-shared/scripts/money.py status
```

`status` returns `ready`, and `next_step` — **the single most useful thing to
ask for right now**, already worked out. Ask for that one thing, write the
answer, call `status` again. Repeat until `next_step` is null.

Never assemble your own idea of what is missing, and never ask for something
`status` did not name. The order exists because each step unblocks the next:
a timezone that is wrong files spending on the wrong day, and income that is
missing inverts every verdict.

## One question per message

Two questions in one text is a form, and a form is what every abandoned
budgeting app opens with. The owner is on a phone.

> Qual cidade você mora? É só pra eu marcar os gastos no dia certo.

> Quanto você recebe, e de quanto em quanto tempo?

> Tem algum gasto fixo todo mês? Aluguel, internet, essas coisas.

After each answer, **confirm what you wrote in one line** and move on. No
recaps, no progress bars, no "step 2 of 4".

## Writing the answers

```sh
S=/opt/data/skills/cfo-shared/scripts
python3 $S/money.py config timezone America/Sao_Paulo
python3 $S/money.py config currency BRL

# --every: monthly (default), weekly, biweekly
python3 $S/money.py fixed add 'salário' '7000' --kind income --day 5
python3 $S/money.py fixed add 'freela' '1500' --kind income --every weekly
python3 $S/money.py fixed add 'aluguel' '1.800,00' --kind expense --day 5
```

**Single quotes, and digits only in an amount.** In `""` the shell expands
`$7.000` to `.000` and files a salary of three cents — silently, and the row
looks fine afterwards. See cfo-log for the incident this comes from.

**Pay frequency is not cosmetic.** Someone paid weekly earns 52 weeks a year,
not 48 — recording a weekly wage as monthly loses them about a month of
income in every projection. Ask *how often*, not just how much, and pass
`--every`. The engine converts to a monthly equivalent itself; never do that
conversion in your head.

Infer the timezone from the city rather than asking for an IANA name — nobody
knows they live in `America/Sao_Paulo`. Same for currency: infer it, then
confirm it in the line where you confirm the city.

**The city sets the clock for everything, including the brief.** The daily
brief fires on the hours in `status.configured` — read in the owner's own
zone, not the container's — so the city answer is also what makes the morning
message arrive in the morning. Never ask for a brief time during setup: 08:00
is the default and one more question is what turns setup into a form.

```sh
python3 $S/money.py config brief_hour 7      # "me manda o resumo às 7"
python3 $S/money.py config brief_hour off    # "para de me mandar de manhã"
```

Only when they ask. If they want to know when it arrives, `status` already
carries the answer — do not guess an hour, and never state one you did not
read.

## Two ways in, and the owner picks

On first contact, offer both in one message — this is the one time two options
are better than one question:

> Posso te configurar em uns 30 segundos, ou você já manda um gasto e a gente
> ajusta no caminho. Como prefere?

If they start logging instead, **let them.** Do not chase the setup. Ask for
income only when something actually needs it — when `status` says
`ready: false` and they have asked a question you cannot answer without it.
That request lands as useful because they already want the answer.

## Sample data

For someone evaluating the agent rather than using it, an empty ledger answers
nothing:

```sh
python3 $S/seed_demo.py            # 3 complete months + the current one to date
python3 $S/seed_demo.py --reset    # removes ONLY demo rows
```

Offer it once. Say plainly that it is sample data and that `--reset` clears it
without touching anything they logged. `status` reports `all_data_is_demo`
when the ledger holds nothing else — check it before describing numbers as
theirs.

## Rules

- **Never invent a timezone, a currency, an income or a payday.** Ask. Every
  one of them is invisible when wrong and expensive later.
- Confirm in one line, then get out of the way.
- If they decline setup, that is a complete answer. Do not ask twice.
