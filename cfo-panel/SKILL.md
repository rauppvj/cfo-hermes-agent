---
name: cfo-panel
description: The wall panel — this month's money as a page on the owner's own Mac, kept current by code and opened on a screen they walk past. Use when the owner asks to see the panel, dashboard, painel or screen, asks where they can look at their money without texting, wants it opened or put on a tablet, or says the panel looks stale or wrong.
---

# The panel

A second surface for the same ledger. The chat answers when asked; the panel
is already showing when someone glances at it — the kitchen tablet, the
second monitor, the old iPad propped against the wall.

**You do not write it.** `panel.py` renders it from the ledger, and no figure
on it passes through you. Your job here is three things: say where it is,
refresh it when someone thinks it is stale, and open it on the Mac when they
ask and Latch is available.

## Where it is

**Read the path; never type it from memory.** It moved, and the two contracts
answer differently:

```sh
python3 $HERMES_HOME/skills/cfo-shared/scripts/panel.py --json | head -5
```

- **`/srv/cfo/panel/index.html`** on the current contract (`$CFO_PANEL` names
  it). The agent's home is a Docker volume there, so the page is written into
  a directory the compose file mounts from the owner's Mac — which is the
  whole point: a page inside a volume is a page nobody can open. On their Mac
  it is `panel/index.html` **inside the checkout they installed from**, and
  `open panel/index.html` from that directory is the whole instruction.
- **`$HERMES_HOME/cfo/panel/index.html`** on an instance from the deprecated
  deployer, whose home IS a folder on the Mac (`~/.hermes-<name>/`).

Either way it opens by double click, in any browser, over no network. There is
no server and no port, and nothing about the page reaches anyone else.

## It refreshes itself

Two things keep it current, both code:

- **every ledger write** — `money.py` redraws the panel after anything that
  changes what it would say, so a spend logged in the chat is on the screen
  before the reply is;
- **a scheduled tick**, so midnight moves "today" and "yesterday" on a page
  nobody has touched.

So if the owner says it looks stale, do not assume it is broken. Ask what
figure looks wrong, and check it against the ledger:

```sh
S=$HERMES_HOME/skills/cfo-shared/scripts
python3 $S/panel.py --json      # what the page is showing
python3 $S/money.py status      # what the ledger says
```

If those two agree, the page in front of them is an old render sitting in a
browser tab — it reloads on its own each minute, and a manual reload settles
it. If they disagree, that is a real bug: say so plainly rather than
redrawing it and hoping.

Redraw it by hand with no arguments — never with `--path`, which writes it
somewhere the owner is not looking:

```sh
python3 $HERMES_HOME/skills/cfo-shared/scripts/panel.py
```

## Opening it on their Mac, when Latch is configured

Latch is optional and most installs do not have it. When this instance has
it, "abre o painel" is one command **on the Mac**, not in this container:

    open <the checkout>/panel/index.html      # current contract
    open ~/.hermes-<name>/cfo/panel/index.html # the deprecated deployer

Read which one this instance is from `panel.py --json`, above — the path it
prints is the one inside the container, and its last two segments
(`panel/index.html`) are what the owner opens on their side of the mount. If
reaching the Mac fails, that is not an error
to report as a failure of the panel — **give them the path** and say it opens
by double click. The panel is a file on their disk either way; Latch only
saves them the walk to Finder.

## What it shows, so you can answer questions about it

Top to bottom: the month's spend as the hero figure and where it closes at
the current pace; today and yesterday; **what is in the account** and **what
is on the card, unpaid**; the top categories, each drawn against its own
budget when one is set (the bar turns red past the ceiling); and the bills
falling due in the next seven days.

**When the month is too young, the panel shows no projection** — the same
rule the brief follows, for the same reason: a pace divided by two days is
one purchase multiplied by thirty. If the owner asks why the projection is
missing, that is the answer, and `money.py project` names what is missing in
`basis.reasons`.

**When there is no balance reading, the account card says so** instead of
showing a number — "tell the agent what is in the account". That is a
`balance set` away (cfo-log). The card figure comes from `money.py balance`;
if the owner disputes it, that command's fields are where to look.

The page is in the owner's language, from `money.py config language`;
absent that setting it follows the currency. If they want it in the other
language, set it and redraw:

```sh
S=$HERMES_HOME/skills/cfo-shared/scripts
python3 $S/money.py config language en && python3 $S/panel.py
```

## Rules

- **Never edit the HTML**, and never write a page of your own. A panel a
  model wrote is a panel that can be quietly wrong at 3am with nobody
  reading it — which is worse than a wrong chat message, where someone is.
- **Never put the panel somewhere it can be reached from outside the Mac.**
  No web server, no tunnel, no upload, no copy into a shared folder — not
  even if asked to "send" it. It is a page with someone's spending on it and
  no login. If they want it on another screen, that screen opens the file
  from the Mac.
- Do not announce the panel unprompted in every reply. It is worth
  mentioning once, when it would help — after an import, or when they ask
  where to see the whole picture.
