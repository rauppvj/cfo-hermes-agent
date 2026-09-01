---
name: cfo-simulate
description: Answer whether the owner can afford a specific purchase, and what it costs them beyond the price — the effect on this month's close, and how instalments change the answer. Use for "can I afford X", "posso comprar um monitor de 2 mil", "should I buy this", "what if I spend 500 on Y", "is it better in 3x or upfront". Do not use for general questions about past spending (cfo-ask) or to record a purchase already made (cfo-log).
---

# Can I afford this?

This is the question a budgeting app can never answer well and a financial
manager answers in one sentence. The whole skill is: get the arithmetic from
the engine, then say the thing the number means.

```sh
python3 /opt/data/skills/cfo-shared/scripts/money.py simulate "<amount>" \
  --installments <n>
```

Returns the month's projection with and without the purchase:
`projected_net_before`, `projected_net_after`, `fits_this_month`, `swing`,
`per_installment`, `first_installment` — each with a `_fmt` twin.

**Every number you say is one of those fields.** You do not work out what is
left over, you do not divide the price yourself, and you do not judge
affordability by feel. `fits_this_month` is the verdict; the rest is how you
explain it.

## Run it more than once

The interesting answer is usually the comparison. When the purchase does not
fit outright, run it again split — `--installments 3`, `--installments 6` —
and lead with the option that fits. That is the difference between "no" and
"not this month, but in 3x yes", and the second one is the useful answer.

## Shape of the reply

Verdict, cost, alternative. Two or three lines, no table:

> Cabe. Mas o mês fecha em R$ 300,00 no vermelho se você mantiver o ritmo.
> Em 3x cabe folgado — R$ 666,68 agora e mais duas de R$ 666,66.

Rules for the words:

- **Answer first.** "Cabe" or "não cabe", then why. Never open with the
  methodology.
- **Say it is a pace, not a prophecy** — the projection assumes the rest of
  the month looks like the start of it, and the owner can change that.
- **Never tell them what to do.** Give them the number and the option; the
  decision is theirs. A tool that says "you shouldn't buy this" gets deleted.
- **A purchase is not recorded.** Simulating is asking, not spending. If they
  then say they bought it, that is `cfo-log`.
- When the ledger is too thin for a projection (a few days, no fixed lines),
  say that instead of producing a confident verdict from almost no data.
