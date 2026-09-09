# Autopilot: purchases that log themselves

The chat is the slow part of a money agent. *"Spent 40 on lunch"* is one
message, but it is one message every time, and the days nobody texts are the
days the month goes wrong. This page removes the typing for most of a month:
**every card tap on the phone, and every purchase the bank texts you about.**

The mechanism is your iPhone's own Shortcuts app. Two of its automation
triggers matter here:

| trigger | fires when | carries |
|---|---|---|
| **Transaction** (iOS 17–18) / **Wallet** (iOS 26) | a card in Apple Wallet is used — in a shop, online, in an app | merchant, amount, card |
| **Message** | an SMS arrives from a sender you choose — your bank | the text of the alert |

Each one runs a shortcut that sends **one iMessage to your agent**, in the
same thread you already use. There is no server between the phone and the
agent, nothing installed, and the agent answers in one line.

```
   tap the phone at the bakery
      ↓ Wallet trigger
   💳 R$ 14,90 · Padaria Central · Nubank        ← sent by the phone
   cfo:  ✓ R$ 14,90 · Padaria Central · food · R$ 612,40 this month

   buy something online with the card number
      ↓ the bank's SMS arrives → Message trigger
   📩 Compra aprovada: R$ 89,90 em LOJA ONLINE, cartão final 4321
   cfo:  ✓ R$ 89,90 · Loja Online · shopping · R$ 702,30 this month
```

A purchase that arrives on **both** channels — an Apple Pay tap the bank also
texts about — is recorded once: the engine drops the second message when the
same amount came from the other channel minutes earlier.

---

## Install the two shortcuts (one tap each)

The shortcuts are built and signed in this repo, so the part people get wrong
by hand — wiring the trigger's fields into the text, turning off the prompt at
the till — comes done. **On your iPhone**, open either link, or scan the QR
from a screen:

| | link | QR |
|---|---|---|
| **cfo · Apple Pay** | [cfo-apple-pay.shortcut](https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/shortcuts/cfo-apple-pay.shortcut) | ![](shortcuts/qr-cfo-apple-pay.png) |
| **cfo · bank SMS** | [cfo-bank-sms.shortcut](https://raw.githubusercontent.com/rauppvj/cfo-hermes-agent/main/docs/shortcuts/cfo-bank-sms.shortcut) | ![](shortcuts/qr-cfo-bank-sms.png) |

Safari downloads the file; tap it in the downloads list (the arrow at the top
right) and it opens in Shortcuts with **Add Shortcut**. On import it asks one
question: **the number you text your agent at** — the same one you sent the
activation code to. Answer it once and the shortcut is ready.

> If the question does not appear, open the shortcut, tap the recipient field
> of *Send Message* and put the number there. Once.

You can also ask the agent for the links in the chat — *"how do I stop typing
purchases?"* — and tap them from there.

## Then the one step iOS will not let a file do

An **automation** — the trigger — cannot be shared or installed from a link.
Apple keeps triggers device-local. So each one is created by hand, in one
step, and pointed at the shortcut you just added:

**Apple Pay** — Shortcuts → *Automation* → **+** → **Transaction** (or
**Wallet**) → *Any Card* → **Run Immediately**, *Notify When Run* off → Next →
**Run Shortcut** → pick **cfo · Apple Pay** → Done.

**Bank SMS** — Shortcuts → *Automation* → **+** → **Message** → *Sender*: the
number or name your bank's alerts come from (open one in Messages to see it)
→ **Run Immediately** → Next → **Run Shortcut** → pick **cfo · bank SMS** →
Done. Repeat for a second bank.

Pay for something. The reply arrives like any other message.

## What the agent does with them

A message opening with `💳` or `📩` was not typed; the agent treats it as a
fact, not a sentence:

- **`💳` tap** — logged **on the card** (`--via credit`), because a Wallet
  purchase almost always is, so it shows in this month's spending and in
  *"on the card, unpaid"*, and it does not touch your account balance until
  you say the invoice is paid. If a card in Wallet is a **debit** card, tell
  the agent once: *"Inter is a debit card"*.
- **`📩` alert** — the agent reads the amount, the merchant and, when the
  text says so, débito or crédito, out of the bank's own wording, and logs
  that. An alert that is not a purchase — a Pix received, a boleto scheduled,
  a login warning — is logged as what it is or ignored.
- The **category** comes from the merchant name, the way a bank statement is
  classified, and the name is remembered so the same shop is never asked
  about twice.
- The reply is **one line**: amount, merchant, category, month so far. No
  question, no follow-up. To correct one: *"that last one was groceries"*, or
  *"undo"*.

## What it does not cover, honestly

- **Push notifications from a bank app** (the kind with no SMS) cannot be
  read by Shortcuts — iOS exposes no notification trigger. If your bank
  alerts you only in-app, the tap channel still catches Apple Pay, and the
  statement or card invoice catches the rest at month end, deduplicated
  against what was already logged.
- **Pix, boletos and cash** pass through neither channel unless the bank
  texts about them. Say them in the chat as before.
- The Transaction trigger is known to **time out** on some phones and the
  automation quietly does not fire (Apple's own forums carry the reports).
  The evening brief is the safety net: when the day logged nothing, it asks.
- Android has no Wallet trigger. Automation apps there (MacroDroid, Tasker)
  can forward notifications to an SMS; the agent reads a `📩` message the
  same way whoever sent it.

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
that the ledger is a file on your own Mac. Wallet taps, the bank's own texts,
receipts and statement files keep that promise; a bank aggregator does not,
and if it is ever added it will be optional and named as the trade it is.

## Rebuilding the shortcuts

They are generated, not hand-made: [`shortcuts/build.py`](shortcuts/build.py)
writes the two files as plists and a Mac signs them —

```sh
python3 docs/shortcuts/build.py /tmp/unsigned
shortcuts sign --mode anyone -i /tmp/unsigned/cfo-apple-pay.shortcut -o docs/shortcuts/cfo-apple-pay.shortcut
shortcuts sign --mode anyone -i /tmp/unsigned/cfo-bank-sms.shortcut  -o docs/shortcuts/cfo-bank-sms.shortcut
```

— because iOS refuses to import an unsigned shortcut file. No number and
nothing of anyone's is inside them; the recipient is asked on install.
