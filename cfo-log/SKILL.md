---
name: cfo-log
description: Record money moving, from whatever the owner sends — a sentence ("spent 40 on lunch", "gastei 87 no uber", "got paid"), several days at once ("Saturday I paid X, Sunday Y"), a card tap forwarded by their phone (a message opening with 💳), a photo of a receipt, a statement of what is in their account ("I have 1.748 in the account"), or a card invoice being paid. Also for corrections ("that was groceries", "undo", "apaga o último"). Do not use for questions about past spending (cfo-ask) or affordability (cfo-simulate).
---

# Log what happened

The owner tells you, in the words they'd use with a person, that money moved.
Your job is to turn that into the right engine call — one per transaction —
and confirm it with the figures the engine returns.

**Read this first: you do not do arithmetic.** Not the total, not the running
sum, not the category subtotal, not "that's about 12% of your budget". You
call the CLI and you read the fields it returns. A model that adds a column of
numbers will one day add them wrong and say it with the same confidence — in
someone's budget, that is the whole product broken. Every number you say back
comes from a field in the JSON.

## The call

```sh
python3 $HERMES_HOME/skills/cfo-shared/scripts/money.py add '<amount>' \
  --kind expense|income --category <category> --note '<what they said>' \
  [--on <day>] [--via debit|credit] [--card '<card name>']
```

**Single quotes, and digits only in the amount.** Both halves are one lesson,
learned the expensive way. The owner texted *"I just spent $54.82 on the
market"*; the call went out as `add "$54.82" --note "I just spent $54.82 on
the market"`; and the shell expanded `$5` — an unset positional parameter — to
nothing. R$ 4,82 went into the ledger, with a note that read "I just spent
4.82 on the market". The amount and the note agreed with each other, so
nothing after that point could tell. Inside `''` the shell expands nothing,
and an amount with no symbol has nothing to eat.

Keep the digits **exactly as the owner wrote them** — `40`, `40,50`,
`1.234,56`, `1,234.56` all read correctly, and re-typing one into another
format is how a thousands separator becomes a decimal point and R$ 1.234,56
becomes R$ 1,23. Strip only the currency symbol; `add` refuses one anyway.

If the response carries a `warning`, do not confirm the amount — ask the
owner about it in one short question first.

`add` returns the new row plus the month's totals. Confirm with those.

## The day it happened: `--on`

People catch up. *"Saturday I paid the car, Sunday the market, Monday 1.240"*
is three days, said on Tuesday. **Pass the day the owner named**, and the
engine stamps the row on that day in their zone:

```sh
... add '118,60' --category transport --note 'car bill' --on saturday
... add '64,35'  --category groceries --note 'market'   --on sunday
... add '1.240,00' --category other  --note 'Monday expense' --on monday
```

`--on` takes `YYYY-MM-DD`, `today`/`hoje`, `yesterday`/`ontem`, `anteontem`,
or a weekday in either language (`saturday`, `sábado`, `sab`) — meaning the
most recent one. It refuses the future. **No `--on` means now**, which is
right for "just spent 40 on lunch" and wrong for anything with a day in it.

This rule used to say the opposite — never pass a date — and one evening four
days of catching up landed on one date. The next morning's brief opened with
"yesterday: R$ four days' worth" and a month projected at a five-figure negative close.
Every figure was sourced; only the days were wrong. The reply confirms the day
when it is not today: *"Anotado no sábado: R$ 118,60 em transporte."*

## How it was paid: `--via`

The engine keeps two kinds of money leaving:

- **`debit`** (the default) — pix, cash, boleto, a debit card, a transfer.
  Left the account when it happened.
- **`credit`** — a card that settles on an invoice. It is spending **today**
  (it counts in this month's categories) but it leaves the account only when
  the invoice is paid. The row carries `card_open_cents` back: what the next
  invoice already holds.

Pass `--via credit` whenever the owner says card, cartão, crédito, fatura, or
the message is a Wallet tap (below). Pass nothing for pix, débito, dinheiro,
boleto. Ask only when it genuinely changes the answer and nothing in the
message says — *"foi no cartão ou no pix?"* is one short question, and it is
worth it for anything large.

**Paying the card is not spending.** *"Paguei a fatura, 2.841,17"*, *"paid my
card bill"* is money leaving the account to settle purchases already in the
ledger. Counted again as an expense it doubles the month — it did, once. It
is its own call, and never `add`:

```sh
python3 .../money.py card pay '2.841,17' [--on saturday]
```

It returns `kind: transfer` and `card_open_cents` (what is still open after
it). Confirm it as a payment, not as spending: *"Fatura paga, R$ 2.841,17.
Nada mais em aberto no cartão."*

## What is in the account: `balance set`

*"I have 1.312,40 in my account"*, *"meu saldo é 980"*, *"sobrou 900 na
conta"* — a **reading**, not a transaction. Never turn it into an income or
an expense to make the numbers meet; that is a fake row that will be wrong
for the rest of the month. Record it as what it is:

```sh
python3 .../money.py balance set '1.312,40' [--on yesterday]
```

From that instant the engine keeps the balance current on its own: plus
income, minus debit spending and card payments, and card purchases do not
touch it until the invoice is paid. `balance set` returns the balance and
what is open on the card; confirm both in one line and move on. Whenever the
owner reads a fresh figure off the bank, set it again — the newest reading
wins.

This is the question people ask most — *"how much do I have?"* — and it had
no answer until the owner supplied one. If they never have, `status` says
so in `next_step`; ask once, when it comes up.

## A card tap the phone forwarded

A message that opens with `💳` was not typed. The owner's iPhone sent it the
moment a card in Wallet was used (see `docs/AUTOPILOT.md`), in the shape

    💳 R$ 14,90 · Padaria Central · Nubank

Log it with no questions and no ceremony:

```sh
python3 .../money.py add '14,90' --category food --note 'Padaria Central' \
  --card 'Nubank' --source wallet
```

- `--card` resolves the method: **credit** unless the owner told you that
  card is debit (*"Inter is a debit card"* → `money.py card set Inter debit`,
  once; `card list` shows what is known).
- The category comes from the merchant name, the way an import is
  classified. If it is genuinely unclear, `other` — a one-line reply is not
  the place for a question, and `uncategorized` will surface it later.
- **Reply in one line, no question:** amount, merchant, category, month so
  far. *"✓ R$ 14,90 · Padaria Central · alimentação · R$ 612,40 no mês."*
  Nothing else. Twenty of these a week is fine; twenty questions is not.

## A photo of a receipt

The owner can send an image — a receipt, a Pix confirmation, a restaurant
bill — and you can see it. Read the **total**, the **merchant** and the
**date** off it; if the date is not today, pass `--on`; if it names the
payment method, pass `--via`. Then confirm the one line you are about to
write before writing it: *"R$ 86,40 no Mercado São José, ontem, no débito —
anoto?"* One question, because a misread total is a wrong number in the
ledger that looks exactly like a right one.

Never write anything from an image without saying what you read. Never log
a picture that has no total on it.

## Categories

Pick one: `food` `groceries` `transport` `housing` `utilities` `health`
`education` `shopping` `leisure` `subscriptions` `fees` `other`.

- `food` is eating out and delivery; `groceries` is a shop.
- When it is genuinely ambiguous, pick the likelier one and **say which you
  picked** — "anotei em transporte" — so a wrong guess is corrected in one
  message instead of quietly skewing a month.
- Never invent a category outside the list.

## Corrections and undo

- *"That was groceries, not food"*, *"the rent was 1.500, not 1.000"*,
  *"that was Saturday"*: change the row in place, keeping its id:

  ```sh
  python3 .../money.py edit <id> [--category groceries] [--amount '1.500'] \
    [--on saturday] [--via credit] [--note '...'] [--kind income]
  ```

  `edit` returns `before` and `after`; confirm with `after`. The id is in the
  confirmation you sent, or in `recent`.
- *"Undo"*, *"apaga o último"*, *"esquece isso"*:
  `money.py delete --last` removes the newest row logged in the chat (never
  a demo row, never an imported one — imports undo by batch in cfo-import)
  and returns what it removed; say what went.
- *"That card bill I logged was actually a payment"*:
  `edit <id> --kind transfer --category card_payment` — hmm, `edit` accepts
  `--kind expense|income` only; delete the row and record it with
  `card pay --on <day>` instead.

## Rules

- **One `add` per transaction.** "Gastei 40 no almoço e 20 no uber" is two
  calls, two categories. A multi-day message is one call per line, each with
  its own `--on`.
- **Income needs `--kind income`.** Default is expense; a salary filed as an
  expense inverts the month.
- **Never guess an amount.** "Gastei uma grana no mercado" gets a question,
  not a number. An invented amount is worse than no record.
- **A stated balance is `balance set`, never a transaction.**
- **A paid invoice is `card pay`, never an expense.**
- **Never date-shift to work around what you think today is.** `--on` is for
  the day the owner named; the engine already knows today in their zone.
- Report a tool error in one sentence and stop. Never say something was
  recorded unless the call returned an id.

## Answering back

Short, and with the totals from the response — the way someone who keeps your
books would say it:

> Anotado: R$ 40,00 em alimentação. Você está em R$ 512,00 esse mês.

> Anotado no sábado: R$ 118,60 em transporte, no cartão — R$ 1.519,81 em
> aberto na fatura.

Not a table, not a receipt, not a congratulation. One or two lines, in the
language the owner wrote in.
