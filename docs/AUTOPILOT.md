# Autopilot: purchases that log themselves

The chat is the slow part of a money agent. *"Spent 40 on lunch"* is one
message, but it is one message every time, and the days nobody texts are the
days the month goes wrong. This page removes the typing for most of a month,
through three channels that already exist on your phone and your Mac. None
of them needs an account anywhere, and none of them sends your data to
anyone but your own agent.

| channel | catches | needs |
|---|---|---|
| **Wallet tap** | every Apple Pay purchase — in a shop, online, in an app | one shortcut + one automation on the iPhone |
| **bank SMS** | every purchase your bank texts about — physical card included | one shortcut + one automation on the iPhone |
| **bank push** | every purchase your bank app notifies — physical card, débito, Pix; the banks that never send SMS | the Mac: iPhone Mirroring + one script |

One purchase often arrives on two of them within a minute. It is recorded
**once**, and the bank's text decides one thing the tap cannot know: whether
it was **débito or crédito**.

```
   tap the phone at the bakery
      ↓ Wallet trigger
   💳 R$ 14,90 · Padaria Central · Nubank             ← sent by the phone
   cfo:  ✓ R$ 14,90 · Padaria Central · food · crédito · R$ 612,40 this month

   the bank's push arrives a minute later, mirrored to the Mac
      ↓ notify_watch.py → inbox → notify_gate.py
   "Compra no débito aprovada: R$ 14,90 em PADARIA CENTRAL"
   cfo:  (nothing to say: same purchase, now recorded as débito)

   pay with the physical card at the market
      ↓ push only, no tap
   cfo:  ✓ R$ 187,40 · Mercado São José · groceries · débito
```

---

## Débito or crédito: who decides

In Brazil one card is both functions, and Apple Wallet does not report which
one a tap used. So:

1. A **tap** is recorded as crédito, unless you told the agent that card is a
   debit card (*"Inter is a debit card"* → remembered), or unless you say
   otherwise afterwards: reply **"débito"** to the confirmation and the row
   is corrected.
2. The **bank's own text** — SMS or push — says "no débito" / "no crédito"
   / "parcelado em 3x", and that reading is authoritative. When it arrives
   after the tap for the same amount, the tap's row is **corrected** to what
   the bank said, not duplicated.
3. **Pix** is always the account (débito). **Estorno / refund** is money
   coming back.

Why it matters: crédito is spending today but leaves the account only when
the invoice is paid; débito leaves now. *"How much do I have?"* depends on
telling them apart, and so does *"what is on the card?"*.

---

## Channel 1 and 2: the iPhone shortcuts

The shortcuts are built and signed in this repo, so the part people get wrong
by hand — wiring the trigger's fields into the text, turning off the prompt at
the till — comes done. **On your iPhone**, open either link, or scan the QR
from a screen:

| | link | QR |
|---|---|---|
| **cfo · Apple Pay** | [cfo-apple-pay.shortcut](https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/shortcuts/cfo-apple-pay.shortcut) | ![](shortcuts/qr-cfo-apple-pay.png) |
| **cfo · bank SMS** | [cfo-bank-sms.shortcut](https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/shortcuts/cfo-bank-sms.shortcut) | ![](shortcuts/qr-cfo-bank-sms.png) |

Safari downloads the file; tap it in the downloads list (the arrow at the top
right) and it opens in Shortcuts with **Add Shortcut**. Then open the
shortcut once and set the **recipient** of *Send Message* to the contact you
text your agent at (the same thread you sent the activation code to). That is
the one field a shared shortcut cannot carry.

**Then run it once by hand, with the phone unlocked** (tap ▶). iOS asks
whether the shortcut may send messages and read the contact; allow both.
This is not optional: a tap happens with the phone locked, and iOS will not
grant a permission on a locked phone -- the first real tap fails with
*"requires privacy permissions that cannot be granted while your device is
locked"* until you have done this. The manual run sends an empty `💳 · ·`,
which the agent ignores.

You can also ask the agent for the links in the chat — *"how do I stop typing
purchases?"* — and tap them from there.

### Then the one step iOS will not let a file do

An **automation** — the trigger — cannot be shared or installed from a link.
Apple keeps triggers device-local. So each one is created by hand, in one
step, and pointed at the shortcut you just added:

**Apple Pay** — Shortcuts → *Automation* → **+** → **Transaction** (or
**Wallet** on iOS 26) → *Any Card* → **Run Immediately**, *Notify When Run*
off → Next → **Run Shortcut** → pick **cfo-apple-pay** → Done.

**Bank SMS** — Shortcuts → *Automation* → **+** → **Message** → *Sender*: the
number or name your bank's alerts come from (open one in Messages to see it)
→ **Run Immediately** → Next → **Run Shortcut** → pick **cfo-bank-sms** →
Done. Repeat for a second bank.

Pay for something. The reply arrives like any other message.

> The three fields inside the shortcut — Amount, Merchant, Card — show as
> plain "Shortcut Input" when you look at them on the phone, because a
> standalone shortcut does not know its input will be a transaction. They
> resolve when the automation runs. The shortcut also sends the transaction
> as a second line, so nothing is lost if a field comes back empty.
>
> Running the shortcut by hand sends `💳 · ·` with no transaction behind it.
> The agent ignores that.

---

## Channel 3: the bank's push notifications, through the Mac

Most Brazilian banks alert by push, not SMS — and iOS gives Shortcuts no way
to read a push. The Mac can. With **iPhone Mirroring** set up, your iPhone's
notifications appear on the Mac, and macOS keeps them in a local database.
A small script reads that database once a minute and drops the ones that
carry an amount into the agent's inbox, on the same Mac the agent already
runs on. The container picks them up every five minutes, records them, and
the agent says what it recorded in one line.

**On the Mac**, from the repo:

```sh
scripts/install-notify.sh          # installs a launchd job; prints the two toggles
```

Then the two things a script cannot do for you, once each:

1. **Full Disk Access** for `/usr/bin/python3` (System Settings → Privacy &
   Security → Full Disk Access → **+** → ⌘⇧G → `/usr/bin/python3`). This is
   what lets it read Notification Center's database.
2. **iPhone notifications on the Mac**: open the *iPhone Mirroring* app once
   and pair; then System Settings → Notifications → **Allow notifications
   from iPhone** → on, with your bank apps allowed.

Check it:

```sh
/usr/bin/python3 cfo-shared/scripts/notify_watch.py --check      # readable?
/usr/bin/python3 cfo-shared/scripts/notify_watch.py --dump 20    # the newest notifications; $ marks an amount
tail ~/.hermes-cfo/logs/notify.log
```

What it forwards: only notifications with an amount in them — from any
app, so a bank you did not think to list still counts. What it never
forwards: anything else on your screen. The file it writes is
`~/.hermes-cfo/inbox/notifications.jsonl`, and you can read it.

The watcher starts from **now**: last week's notifications are last week's
purchases, and the statement import is the honest way to get those.

Requirements: macOS 15 or newer, iOS 18 or newer, the same Apple ID on
both, and iPhone Mirroring available in your region (it is in Brazil; it is
not in the EU).

---

## What the agent does with all of it

A message opening with `💳` or `📩`, or a line the Mac forwarded, was not
typed; the agent treats it as a fact, not a sentence:

- the **amount, merchant and function** are read by code (`bankalert.py`),
  never guessed — an alert with no amount in it is not logged, ever;
- the **category** comes from the merchant name, the way a bank statement is
  classified, and the name is remembered so the same shop is never asked
  about twice;
- the reply is **one line**: amount, merchant, category, débito/crédito when
  it matters, month so far. No question, no follow-up. To correct one:
  *"that last one was groceries"*, *"débito"*, or *"undo"*.

## What it does not cover, honestly

- **Cash.** Say it in the chat.
- A bank that neither texts nor pushes, or a purchase made while the Mac was
  off: the **statement import** at month end catches it, deduplicated
  against what was already logged.
- The Transaction trigger is known to **time out** on some phones and the
  automation quietly does not fire (Apple's own forums carry the reports).
  The push channel and the evening brief are the safety net.
- Android has no Wallet trigger and no Mac mirroring. Automation apps there
  (MacroDroid, Tasker) can forward notifications as an SMS; the agent reads a
  `📩` message the same way whoever sent it.

## The other direction: photos and files

Anything with a total on it can be **sent to the chat as an image** — a
receipt, a Pix confirmation screenshot, a restaurant bill. The agent reads it
and asks you to confirm the one line it is about to write. Bank statements
and card invoices go in the same way, as attachments; that import is the
[`cfo-import`](../cfo-import/SKILL.md) skill and it covers ninety days at
once.

## Why not connect the bank directly

Open Finance links (Pluggy, Belvo, Plaid) are the real answer and the wrong
one for this agent right now: every one of them puts a company between you
and your bank, needs an account with that company, and stores your
transactions on their side to serve them to you. The whole promise here is
that the ledger is a file on your own Mac. Wallet taps, the bank's own texts
and notifications, receipts and statement files keep that promise; a bank
aggregator does not, and if it is ever added it will be optional and named
as the trade it is.

## Rebuilding the shortcuts

They are generated, not hand-made: [`shortcuts/build.py`](shortcuts/build.py)
writes the two files as plists and a Mac signs them —

```sh
python3 docs/shortcuts/build.py /tmp/unsigned
shortcuts sign --mode anyone -i /tmp/unsigned/cfo-apple-pay.shortcut -o docs/shortcuts/cfo-apple-pay.shortcut
shortcuts sign --mode anyone -i /tmp/unsigned/cfo-bank-sms.shortcut  -o docs/shortcuts/cfo-bank-sms.shortcut
```

— because iOS refuses to import an unsigned shortcut file. No number and
nothing of anyone's is inside them.
