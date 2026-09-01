---
name: cfo-brief
description: Send the owner's daily financial brief — yesterday's spend, where the month is heading at the current pace, and anything that needs a decision — as one short proactive message. Runs from the shipped cron each morning; also use when the owner asks for a brief, a summary of where they stand, or "how am I doing" in a way that wants the whole picture rather than one figure.
---

# The morning brief

One message, unprompted, once a day. It is the only time this agent speaks
first, which is exactly why it has to be worth reading — a proactive message
that says nothing gets the whole agent muted, and a muted agent is a deleted
one.

## Gather

```sh
S=/opt/data/skills/cfo-shared/scripts
python3 $S/money.py day            # YESTERDAY -- the first sentence
python3 $S/money.py project        # pace, projected close, fixed lines
python3 $S/money.py summary        # month to date, by category
python3 $S/money.py recent --limit 10
```

`day` defaults to yesterday in the owner's zone and is the only source for
the opening figure. Run it first; without it there is no honest way to say
what yesterday cost, and a brief that opens with the monthly projection is a
bank app rather than a manager.

Read fields. **No arithmetic of your own** — including "that's R$ 40 more than
yesterday", unless both numbers came from a command.

## Write

Three sentences at most, in this order, and drop any that has nothing to say:

1. **What yesterday cost.** One number.
2. **Where the month lands at this pace**, and how that sits against the
   fixed lines and income — **only if `project` returns `basis.usable: true`.**
   In the first days of a month it is false, because a pace divided by one or
   two days is a purchase multiplied by thirty. Then say the month is too
   young to read and give what is solid instead: what is already spent, and
   what fixed lines are due. Never state a projected close over
   `basis.usable: false`; the figure is real arithmetic on too little, which
   is the one kind of wrong number nobody can spot.
3. **The one thing worth a decision today** — a bill that lands this week, a
   category that has already passed last month's total, a projection that
   turned negative. If nothing qualifies, say nothing; do not manufacture an
   insight to fill the slot.

> Bom dia. Ontem R$ 87,00. No ritmo atual setembro fecha em R$ 3.240,00,
> R$ 400,00 acima do previsto. Alimentação já passou o mês inteiro de agosto.

And on the 1st, when there is no pace to read yet:

> Bom dia. Ontem R$ 86,79. Setembro mal começou, então ainda não dá para
> falar em ritmo — o que está certo é R$ 2.480,00 de contas fixas, a primeira
> no dia 6.

## Rules

- **Never invent a reason.** You know what was spent, not why. "Você gastou
  mais porque saiu no fim de semana" is a guess presented as a fact.
- **No streaks, no scores, no encouragement.** This is a manager's note, not
  a fitness app.
- **Silence is a valid brief.** A quiet month with nothing due deserves one
  line, or none. Do not pad.
- If the ledger is empty or holds only `source: demo` rows, do not send a
  brief that reads like the owner's own money. Say it is the sample, once.
- A failed command is reported, not worked around. Never publish a brief with
  a number you could not source.
