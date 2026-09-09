# Autopilot: every card tap logged, without typing

The chat is the slow part of a money agent. *"Spent 40 on lunch"* is one
message, but it is one message every time, and the days nobody texts are the
days the month goes wrong. This page removes the typing for the purchases
that make up most of a month: **anything paid with the phone.**

The mechanism is your iPhone's own Shortcuts app. Since iOS 17 it has a
**Transaction** trigger (called **Wallet** on iOS 26) that fires every time a
card in Apple Wallet is used — Apple Pay in a shop, online, in an app — and
hands the automation the **merchant, the amount and the card**. The shortcut
turns that into one iMessage to your agent. The agent logs it. You did
nothing.

```
   tap the phone at the bakery
      ↓ Wallet transaction trigger (iOS)
   Shortcut composes:  💳 R$ 14,90 · Padaria Central · Nubank
      ↓ Send Message (iMessage, no confirmation)
   cfo:  ✓ R$ 14,90 · padaria · food · R$ 612,40 this month
```

Nothing is installed. There is no server between the phone and the agent —
it is the same iMessage thread you already use, sent by the phone itself.

---

## Set it up (three minutes, once)

1. Open **Shortcuts** → **Automation** tab → **+** (New Automation).
2. Pick **Transaction** (iOS 17–18) or **Wallet** (iOS 26).
3. **Card**: *Any Card* — or only the cards you want logged.
4. Choose **Run Immediately**, and turn **Notify When Run** off. This is what
   makes it silent: no "Run?" prompt at the till.
5. **Next** → **New Blank Automation**, then add two actions:

   **Text** — type exactly this, inserting the variables from the trigger
   (tap *Shortcut Input* → pick the field):

   ```
   💳 [Amount] · [Merchant] · [Card]
   ```

   **Send Message** — Recipient: the number you texted your activation code
   to (the agent's own iMessage number). Message: the Text above.
   Turn **Show When Run** off so it sends without opening Messages.

6. **Done.** Pay for something. The reply arrives like any other.

> The three fields the trigger provides are *Amount*, *Merchant* and
> *Card (Pass)*. `[Amount]` already carries your currency symbol; the agent
> strips it. If your iPhone is set to another language the labels differ,
> the shape is the same.

## What the agent does with it

A message opening with `💳` is a tap, not a sentence. The agent:

- logs it **on the card** (`--via credit`), because that is what a Wallet
  purchase almost always is — so it shows in this month's spending and in
  *"on the card, unpaid"*, and it does not move your account balance until
  you tell the agent you paid the invoice;
- picks the category from the merchant name, the way it does for a bank
  statement, and remembers the name so the same shop is never asked twice;
- answers in **one line**: amount, merchant, category, month so far. No
  question, no follow-up.

If a card in Wallet is a **debit** card, tell the agent once:
*"Inter is a debit card"* → `money.py card set Inter debit`, and taps on that
card leave the account immediately from then on.

To correct one: *"that last one was groceries"*, or *"undo"*.

## What it does not cover, honestly

- **Pix, boletos, cash, and the physical card** never pass through Wallet, so
  they never trigger this. Say them in the chat as before, or let the
  statement import catch them at month end — the two are deduplicated, so
  logging a purchase *and* importing the statement it appears on records it
  once.
- Apple's own note: the Transaction trigger can **time out** on some phones
  and the automation quietly does not fire. It is a known iOS behaviour, not
  something the shortcut can fix. The evening brief is the safety net: when
  the day logged nothing, it asks.
- Android has no equivalent trigger.

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
that the ledger is a file on your own Mac. Wallet taps, receipts and
statement files keep that promise; a bank aggregator does not, and if it is
ever added it will be optional and named as the trade it is.
