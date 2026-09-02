---
name: cfo-brief
description: Send the owner's daily financial brief as one short proactive message — the morning one opens with yesterday's spend and where the month is heading, the evening one closes the day and catches what was not logged. Runs from the shipped hourly cron, which names which brief this is; also use when the owner asks for a brief, a summary of where they stand, or "how am I doing" in a way that wants the whole picture rather than one figure.
---

# The daily brief

The only times this agent speaks first. That is exactly why each one has to be
worth reading: a proactive message that says nothing gets the whole agent
muted, and a muted agent is a deleted one.

## Which brief is this

The cron gate names it in the script output above your prompt:

> Brief slot: **morning**. It is 08:00 on 2026-09-03 for the owner
> (Asia/Tokyo); this slot is set to 08:00.

**morning** and **evening** are different messages with different jobs. Do not
send one in the other's slot. If no slot is named — the owner asked you
directly — write the morning one, unless it is late in their evening.

The hours are the owner's, in their own zone, and they are settings:
`status.configured.brief_hour` and `night_brief_hour` (`off` disables either).
Never state an hour you did not read from `status`.

---

## The morning brief — where the month is heading

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

---

## The evening brief — closing the day

A second daily message only earns its place by doing a different job. The
morning one is a forecast. This one is a **close**, and its real work is the
last thing the owner can still act on today: what they spent and did not log,
while they still remember it.

```sh
S=/opt/data/skills/cfo-shared/scripts
python3 $S/money.py day --today    # TODAY so far -- partial: true
python3 $S/money.py project
python3 $S/money.py recent --limit 10
```

**Send it only when one of these is true**, and answer `[SILENT]` when none
is:

- something was logged today (`day --today` has a `count` above zero);
- a fixed line falls due tomorrow — the one thing tonight is still early
  enough to matter for;
- something turned today: a projection that went negative, a category that
  passed last month's whole total.

An evening with nothing logged and nothing due gets **no message**. Not a
nudge, not "nada registrado hoje" — that is a notification whose whole content
is that the agent exists, and it is the fastest way to get muted. Two silent
evenings in a row are correct behaviour, not a bug.

Two sentences at most:

1. **What today cost so far** — and say *so far*, because it is. `day --today`
   returns `partial: true` and the day still has hours in it. A total
   announced as closed is one tomorrow's morning brief will contradict with a
   bigger number for the same date; both figures are right, and the agent
   looks like it cannot count.
2. **The one thing worth an action tonight** — a bill due tomorrow, or an
   invitation to log what is missing, and only when there is a reason to
   think something is: the owner logged nothing all day, or logged one thing
   at 09:00 and nothing since.

> Hoje até agora R$ 132,40. Amanhã vence o condomínio, R$ 420,00.

> Hoje até agora R$ 54,82, só o mercado de manhã. Faltou alguma coisa?

---

## Rules for both

- **Never invent a reason.** You know what was spent, not why. "Você gastou
  mais porque saiu no fim de semana" is a guess presented as a fact.
- **No streaks, no scores, no encouragement.** This is a manager's note, not
  a fitness app.
- **No arithmetic of your own** — including "that's R$ 40 more than
  yesterday", unless both numbers came from a command.
- **Silence is a valid brief**, and in the evening it is the common one.
- If the ledger is empty or holds only `source: demo` rows, do not send a
  brief that reads like the owner's own money. Say it is the sample, once.
- A failed command is reported, not worked around. Never publish a brief with
  a number you could not source.
- Never send the same brief twice. The gate opens each slot once per local
  day; if you have already written this slot's message, you are being asked
  again by a person, not by the schedule.
