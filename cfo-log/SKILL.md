---
name: cfo-log
description: Record a spend or an income the owner mentions in chat, resolving the amount and category from their own words, and confirm it with the month's running total. Use whenever the owner reports money moving — "spent 40 on lunch", "gastei 87 no uber", "got paid", "put 200 of fuel in the car" — including when several are mentioned in one message. Do not use for questions about past spending (cfo-ask) or affordability (cfo-simulate).
---

# Log a transaction

The owner texts what happened in the words they'd use with a person. Your job
is to turn that into one `money add` call per transaction — nothing more.

**Read this first: you do not do arithmetic.** Not the total, not the running
sum, not the category subtotal, not "that's about 12% of your budget". You
call the CLI and you read the fields it returns. A model that adds a column of
numbers will one day add them wrong and say it with the same confidence — in
someone's budget, that is the whole product broken. Every number you say back
comes from a field in the JSON.

## The call

```sh
python3 /opt/data/skills/cfo-shared/scripts/money.py add "<amount>" \
  --kind expense|income --category <category> --note "<what they said>"
```

Pass the amount **as the owner wrote it**. The engine reads `40`, `40,50`,
`R$ 1.234,56` and `1,234.56` correctly; re-typing it into another format is
how a comma becomes a decimal point and R$ 1.234,56 becomes R$ 1.23.

`add` returns the new row plus the month's totals. Confirm with those.

## Categories

Pick one: `food` `groceries` `transport` `housing` `utilities` `health`
`education` `shopping` `leisure` `subscriptions` `fees` `other`.

- `food` is eating out and delivery; `groceries` is a shop.
- When it is genuinely ambiguous, pick the likelier one and **say which you
  picked** — "anotei em transporte" — so a wrong guess is corrected in one
  message instead of quietly skewing a month.
- Never invent a category outside the list.

## Rules

- **One `add` per transaction.** "Gastei 40 no almoço e 20 no uber" is two
  calls, two categories.
- **Income needs `--kind income`.** Default is expense; a salary filed as an
  expense inverts the month.
- **Never guess an amount.** "Gastei uma grana no mercado" gets a question,
  not a number. An invented amount is worse than no record.
- **Do not date-shift.** The engine stamps the transaction in the owner's own
  timezone. Never pass a date to work around what you think today is.
- If the owner corrects a mistake, `money.py delete <id>` and add it again —
  the id is in the confirmation you just sent.
- Report a tool error verbatim and stop. Never say something was recorded
  unless the call returned an id.

## Answering back

Short, and with the totals from the response — the way someone who keeps your
books would say it:

> Anotado: R$ 40,00 em alimentação. Você está em R$ 512,00 esse mês.

Not a table, not a receipt, not a congratulation. One or two lines.
