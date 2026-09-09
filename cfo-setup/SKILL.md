---
name: cfo-setup
description: Walk a new owner through the short setup that makes the rest of the agent work — timezone, currency, the language they write in, what they earn and how often, their fixed monthly costs, and what is in their account today — one question at a time; set a budget per category; name a card as debit; offer the phone autopilot; load or clear sample data. Use on first contact, whenever `money status` reports `ready: false` or a `next_step`, when the owner asks to set things up or change their income, salary date, pay frequency, currency, timezone or language, asks what time the brief arrives or wants it at a different hour (or stopped), wants a spending limit for a category ("me avisa se passar de 800 em comida"), or asks how to stop typing every purchase.
---

# Setting someone up

Until this is done the agent cannot do its job — and it fails in the worst
possible way. With no income on file, projected income is zero, so *every*
purchase comes back unaffordable, including a coffee, and the wrong answer
sounds exactly as confident as a right one. With no balance on file, *"how
much do I have?"* — the question people ask most — has no answer at all.

So setup is not paperwork. It is the difference between an agent that works
and one that lies.

## First: the language they write in

Before anything else, on first contact, store the language of the message in
front of you — once, silently, no question:

```sh
python3 $HERMES_HOME/skills/cfo-shared/scripts/money.py config language en   # or pt, es...
```

The chat mirrors the owner's language on its own. The **brief** and the
**panel** cannot — they have no message to mirror — and read this setting.
Without it a Brazilian-currency owner who writes in English gets a Portuguese
morning brief; that happened. If they later ask for the other language
(*"fala comigo em português"*), set it again.

## Let the ledger tell you what to ask

```sh
python3 $HERMES_HOME/skills/cfo-shared/scripts/money.py status
```

`status` returns `ready`, and `next_step` — **the single most useful thing to
ask for right now**, already worked out. Ask for that one thing, write the
answer, call `status` again. Repeat until `next_step` is null.

Never assemble your own idea of what is missing, and never ask for something
`status` did not name. The order exists because each step unblocks the next:
a timezone that is wrong files spending on the wrong day, income that is
missing inverts every verdict, and the balance only stays right once income
and bills are known.

## One question per message

Two questions in one text is a form, and a form is what every abandoned
budgeting app opens with. The owner is on a phone.

> Qual cidade você mora? É só pra eu marcar os gastos no dia certo.

> Quanto você recebe, e de quanto em quanto tempo?

> Tem algum gasto fixo todo mês? Aluguel, internet, essas coisas.

> Quanto tem na sua conta agora? Com isso eu te digo quanto sobra, não só
> quanto saiu.

After each answer, **confirm what you wrote in one line** and move on. No
recaps, no progress bars, no "step 2 of 4".

## Writing the answers

```sh
S=$HERMES_HOME/skills/cfo-shared/scripts
python3 $S/money.py config timezone America/Sao_Paulo
python3 $S/money.py config currency BRL

# --every: monthly (default), weekly, biweekly
python3 $S/money.py fixed add 'salário' '7000' --kind income --day 5
python3 $S/money.py fixed add 'freela' '1500' --kind income --every weekly
python3 $S/money.py fixed add 'aluguel' '1.800,00' --kind expense --day 5

python3 $S/money.py balance set '1.312,40'          # what is in the account now
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

**The balance is a reading, not a transaction.** `balance set` records what
the owner saw on the bank; from then on the engine keeps it current with
every row. Whenever they read a fresh figure, set it again. Never file it as
income.

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

## Budgets: a ceiling per category

*"Me avisa se eu passar de 800 em alimentação"*, *"quero gastar no máximo 300
com lazer"*:

```sh
python3 $S/money.py budget set food '800'
python3 $S/money.py budget remove food
python3 $S/money.py budget                   # each one, with spent / left / pct
```

Categories are the engine's twelve (`budget set` refuses any other). Confirm
in one line — *"Teto de R$ 800,00 em alimentação. Você está em R$ 434,51."*
— quoting the row `budget` returns. The brief and the panel take it from
there: at 80% it is named, past it the bar turns red. Never suggest a
ceiling the owner did not ask for; offer the feature once, after a month of
data, if at all.

## Cards

A card is **credit** unless the owner says otherwise. *"Inter is my debit
card"* → `money.py card set Inter debit`; `card list` shows what is known.
This matters for the phone autopilot below, where taps arrive with only the
card's name.

## The autopilot: stop typing purchases

When the owner says logging is tedious, or asks whether it can see their
purchases by itself, there is a real answer, and it is two taps plus one
step on their iPhone. Two shortcuts are built and signed in the repo; each
one texts you a purchase the moment it happens:

- **Apple Pay** — every card tap in Wallet, in a shop or online:
  https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/shortcuts/cfo-apple-pay.shortcut
- **the bank's SMS** — every purchase the bank texts about, Apple Pay or not:
  https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/shortcuts/cfo-bank-sms.shortcut
- **the bank's push notifications**, through the Mac — the channel that
  catches the physical card, débito, and the banks that never send SMS
  (Nubank, Inter...). Not a shortcut: on the Mac, in the repo checkout,
  `scripts/install-notify.sh`, then two toggles it prints (Full Disk Access
  for `/usr/bin/python3`; "Allow notifications from iPhone"). Needs iPhone
  Mirroring, so macOS 15+ and iOS 18+.

Send both links in one message, with the one-line summary — *"cada compra
chega aqui sozinha, e eu anoto"* — and the one thing iOS will not let a link
do: the **automation** (the trigger) they create by hand, once, as
*Automation → Transaction (or Wallet) / Message → Run Shortcut → cfo*. The
full page, with the QR codes and what it does not cover (push-only bank
apps, pix, boleto), is
https://github.com/rauppvj/cfo-hermes-agent/blob/main/docs/AUTOPILOT.md.

After adding a shortcut they set the recipient (the contact they text you
at) and **run it once by hand, unlocked**, to grant the message permission
-- otherwise the first real tap fails with "requires privacy permissions
that cannot be granted while your device is locked". Offer all this
**once**, when it is relevant; it is not part of setup.

Photos work too: a receipt or a Pix confirmation sent as an image is read and
logged after one confirmation (cfo-log).

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

The sample includes card purchases, a paid invoice, three budgets and an
opening balance, so every surface has something to show. Offer it once. Say
plainly that it is sample data and that `--reset` clears it without touching
anything they logged. `status` reports `all_data_is_demo` when the ledger
holds nothing else — check it before describing numbers as theirs.

## Rules

- **Never invent a timezone, a currency, an income, a payday or a balance.**
  Ask. Every one of them is invisible when wrong and expensive later.
- Confirm in one line, then get out of the way.
- If they decline setup, that is a complete answer. Do not ask twice.
