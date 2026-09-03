#!/usr/bin/env python3
"""The panel: this month's money as a screen you walk past.

The chat is the agent, and it answers when asked. A panel answers when
nobody asked -- it is the second surface, the one on the kitchen tablet or
the second monitor, and its whole job is that the number is already there
when someone glances at it. That is a different act from reading a message:
nothing to open, nothing to type, no notification to dismiss.

The rule the rest of this repo rests on decides how it is built:

  **THE MODEL READS, THE CODE CALCULATES** -- so the model does not write
  this page. Not the figures, not the HTML. `snapshot()` reads the same
  functions `money.py` prints from, `render()` turns that into a document,
  and no language model is in the loop at any point. A panel written by a
  model is a panel that can be quietly wrong at 3am with nobody watching,
  which is worse than the chat being wrong -- in the chat, someone is
  reading.

Two consequences worth stating, because both were choices:

  * **It refuses to project on too little.** `money.basis()` says whether a
    pace exists; when it does not, the hero figure is what is already spent
    and the projection line is replaced by the fixed lines that are certain.
    The brief obeys this rule in prose (`cfo-brief/SKILL.md`); a panel that
    ignored it would show the same meaningless extrapolation all day, on a
    wall, in 72px type.

  * **Every string from the ledger is escaped.** Merchant names come out of
    imported statements, which SOUL.md classes as untrusted input. A payee
    called `<script>` is a rendering bug in the chat and a live script in a
    browser, so nothing dynamic reaches the page unescaped.

Where it lands: `$CFO_DATA/panel/index.html` -- inside the instance's home,
which on a Plow install is a directory on the owner's own Mac
(`~/.hermes-<name>`). So the page opens with `file://`, over no network, with
no server and no port: the same promise the ledger makes. `CFO_PANEL`
overrides the path for anyone who wants it somewhere else.

When it is written: on every ledger change (`money.py` calls
`refresh_panel()` after a write) and on a schedule, by the `cfo-panel` cron
row -- `no_agent`, so the scheduler runs this file and never wakes the model.
The schedule is not redundant with the write hook: at 00:00 "today" becomes a
different day and "yesterday" a different figure, with nothing written for
either.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

# Same two-place import as brief_gate.py, for the same reason: a cron script
# must be a real file under HERMES_HOME/scripts, where money.py is not a
# sibling. Trying the sibling first keeps a local run and the tests honest.
for _candidate in (Path(__file__).resolve().parent,
                   Path("/opt/data/skills/cfo-shared/scripts")):
    if (_candidate / "money.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

import money  # noqa: E402  (path fixed above; money.py has no dependencies)

# How far ahead the panel looks for a bill. A week is the horizon someone can
# still act on -- move a payment, hold a purchase -- and short enough that the
# list stays a list rather than a copy of the fixed table.
UPCOMING_DAYS = 7

# Rows in the category list. Five bars read from across a room; twelve are a
# table, and a table is what nobody looks at.
TOP_CATEGORIES = 5


# --------------------------------------------------------------------------
# words: the panel is read by a person, so it speaks their language
# --------------------------------------------------------------------------
#
# The chat needs no dictionary -- the model answers in whatever language it
# was texted in. Rendered HTML has no such luxury: these strings are written
# by code, once, and a Brazilian owner reading "spent so far" over their own
# money is a panel that looks like somebody else's product.
#
# `money.py config language pt` sets it. Absent, it is inferred from the
# currency, because that is a setting the owner has certainly answered by the
# time there is anything to show -- an inference, so it stays a default and
# never overwrites a stored choice.
LANGUAGES = {
    "pt": {
        "spent_so_far": "gasto até agora",
        "at_this_pace": "no ritmo atual, fecha em",
        "left_over": "sobra",
        "in_the_red": "no vermelho",
        "too_young": "mês novo ainda — sem ritmo para ler",
        "certain_instead": "o que já é certo: %s em contas fixas",
        "today": "hoje",
        "yesterday": "ontem",
        "so_far": "até agora",
        "nothing_logged": "nada registrado",
        "categories": "categorias do mês",
        "upcoming": "vence em %d dias",
        "day_of_month": "dia %s",
        "tomorrow": "amanhã",
        "due_today": "hoje",
        "nothing_due": "nada nos próximos %d dias",
        "updated": "atualizado %s",
        "demo": "dados de exemplo, não os seus",
        "empty": "nada registrado ainda",
        "empty_hint": "mande uma mensagem para o agente: “gastei 40 no almoço”",
        "months": ("janeiro", "fevereiro", "março", "abril", "maio", "junho",
                   "julho", "agosto", "setembro", "outubro", "novembro",
                   "dezembro"),
        "cats": {
            "food": "alimentação", "groceries": "mercado",
            "transport": "transporte", "housing": "moradia",
            "utilities": "contas", "health": "saúde",
            "education": "educação", "shopping": "compras",
            "leisure": "lazer", "subscriptions": "assinaturas",
            "fees": "taxas", "other": "outros",
        },
    },
    "en": {
        "spent_so_far": "spent so far",
        "at_this_pace": "at this pace, closes at",
        "left_over": "left over",
        "in_the_red": "in the red",
        "too_young": "month is too young to read a pace",
        "certain_instead": "what is certain: %s in fixed bills",
        "today": "today",
        "yesterday": "yesterday",
        "so_far": "so far",
        "nothing_logged": "nothing logged",
        "categories": "categories this month",
        "upcoming": "due within %d days",
        # "%s" and the ordinal below, never "the %dth": rent on the 1st read
        # "the 1th" on a wall for a month. English is the language the hosts
        # of the index install in, so this is the copy a stranger meets first.
        "day_of_month": "the %s",
        "ordinal_days": True,
        "tomorrow": "tomorrow",
        "due_today": "today",
        "nothing_due": "nothing due in the next %d days",
        "updated": "updated %s",
        "demo": "sample data, not yours",
        "empty": "nothing logged yet",
        "empty_hint": "text the agent: “40 on lunch”",
        "months": ("January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November",
                   "December"),
        "cats": {},          # the stored names are already English
    },
}

CURRENCY_LANGUAGE = {"BRL": "pt"}


def language(con) -> str:
    """The panel's language: the setting, else inferred from the currency."""
    stored = (money.get_cfg(con, "language") or "").strip().lower()[:2]
    if stored in LANGUAGES:
        return stored
    return CURRENCY_LANGUAGE.get(money.currency_of(con), "en")


def panel_path(con=None) -> Path:
    """Where the page is written.

    Under the instance home by default, which is the owner's own machine --
    never inside this repo, which carries no data. `CFO_PANEL` moves it, for
    an owner who wants it in a folder something else already watches.
    """
    override = os.environ.get("CFO_PANEL")
    if override:
        return Path(override).expanduser()
    return money.data_dir() / "panel" / "index.html"


# --------------------------------------------------------------------------
# snapshot: every figure the page shows, as data, before any markup exists
# --------------------------------------------------------------------------

def snapshot(con, today=None) -> dict:
    """What the panel says, as fields.

    Separate from `render()` so the numbers can be tested without parsing
    HTML -- and so the one rule that matters here is checkable in one
    assertion: `projection` is None whenever `money.basis()` says the month
    cannot carry one.
    """
    today = today or money.now_local(con)
    lang = language(con)
    words = LANGUAGES[lang]
    cur = money.currency_of(con)
    month = today.strftime("%Y-%m")

    st = money.status(con, today=today)
    proj = money.project_month(con, today=today)
    day_now = money.day_totals(con, today.strftime("%Y-%m-%d"), today=today)
    day_prev = money.day_totals(con, money.yesterday_local(con, today=today),
                                today=today)

    cats = []
    biggest = 0
    for row in money.by_category(con, month)[:TOP_CATEGORIES]:
        biggest = biggest or row["total"]        # the first is the largest
        cats.append({
            "category": row["category"],
            "label": words["cats"].get(row["category"], row["category"]),
            "total": row["total"],
            "total_fmt": money.fmt(row["total"], cur),
            # Share of the biggest line, not of the month: bars that compare
            # to each other stay legible when one category is most of the
            # spending, which in a real ledger with rent in it is normal.
            "share": round(100 * row["total"] / biggest) if biggest else 0,
        })

    # Bills only. A salary landing on the 5th is real and the brief may well
    # mention it, but under a heading that reads "due within 7 days" it is
    # money owed, and a panel is read at a glance -- the glance has to be
    # right.
    due = [d for d in money.upcoming_fixed(con, today=today,
                                           within_days=UPCOMING_DAYS)
           if d["kind"] == "expense"]

    return {
        "language": lang,
        "currency": cur,
        "month": month,
        "month_label": words["months"][today.month - 1],
        "year": today.year,
        "updated": today.strftime("%H:%M"),
        "zone": money.get_cfg(con, "timezone") or str(today.tzinfo),
        "spent": st["expense"],
        "spent_fmt": money.fmt(st["expense"], cur),
        "income": st["income"],
        "income_fmt": money.fmt(st["income"], cur),
        # None, not a number, when the month cannot carry a pace. The
        # projection is the one figure on this page that is an extrapolation,
        # and the one the owner cannot check by memory.
        "projection": {
            "expense": proj["projected_expense"],
            "expense_fmt": money.fmt(proj["projected_expense"], cur),
            "net": proj["projected_net"],
            "net_fmt": money.fmt(abs(proj["projected_net"]), cur),
            "in_the_red": proj["projected_net"] < 0,
            "daily_rate_fmt": money.fmt(proj["daily_rate"], cur),
        } if proj["basis"]["usable"] else None,
        "fixed_expense_fmt": money.fmt(proj["fixed_expense"], cur),
        "elapsed_days": proj["elapsed_days"],
        "total_days": proj["total_days"],
        "today": {
            "expense": day_now["expense"],
            "expense_fmt": money.fmt(day_now["expense"], cur),
            "count": day_now["count"],
        },
        "yesterday": {
            "expense": day_prev["expense"],
            "expense_fmt": money.fmt(day_prev["expense"], cur),
            "count": day_prev["count"],
        },
        "categories": cats,
        "days": [{"day": d["day"], "expense": d["expense"],
                  "expense_fmt": money.fmt(d["expense"], cur)}
                 for d in money.daily_totals(con, month, today=today)],
        "upcoming": [{**d, "amount_fmt": money.fmt(d["amount_cents"], cur)}
                     for d in due],
        # Said once, on the page, for the same reason the brief says it: a
        # seeded month that reads as the owner's own is a number they will
        # act on.
        "is_demo": st["all_data_is_demo"],
        "is_empty": st["transactions"] == 0,
        "words": words,
    }


# --------------------------------------------------------------------------
# render: one self-contained file, no network, no framework
# --------------------------------------------------------------------------
#
# Everything is inlined because of where this page runs: opened from
# `file://` on a tablet propped against a wall, often with no working
# network, always with the owner's spending on it. A stylesheet from a CDN
# would be a request to somebody else's server every time the page refreshes,
# carrying a referrer, for a document whose entire promise is that it never
# leaves the machine. So there is nothing external here at all -- one <style>,
# one <svg>, and a meta refresh instead of JavaScript.

STYLE = """
:root {
  color-scheme: dark light;
  --bg: #0d0f12; --panel: #15181d; --line: #23272e;
  --ink: #f2f4f7; --dim: #9aa3ad; --faint: #5b646f;
  --accent: #6ee7a8; --warn: #ff8f7a; --bar: #2b313a;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --line: #e3e6ea;
    --ink: #14181d; --dim: #5b646f; --faint: #9aa3ad;
    --accent: #0f9d58; --warn: #c0392b; --bar: #e3e6ea;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  background: var(--bg); color: var(--ink);
  font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
  padding: clamp(16px, 3vw, 40px);
  display: flex; justify-content: center;
}
.wrap { width: 100%; max-width: 1100px; display: grid; gap: clamp(12px, 1.6vw, 20px); }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
h1 { margin: 0; font-size: clamp(20px, 2.6vw, 30px); font-weight: 620; letter-spacing: -0.01em; }
h1 small { color: var(--faint); font-weight: 500; }
.stamp { color: var(--faint); font-size: clamp(12px, 1.2vw, 14px); white-space: nowrap; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: clamp(14px, 1.7vw, 22px); }
.hero .label { color: var(--dim); font-size: clamp(13px, 1.3vw, 15px); text-transform: lowercase; }
.hero .figure { font-size: clamp(40px, 6.4vw, 76px); font-weight: 660; letter-spacing: -0.03em; line-height: 1; margin: 4px 0 0; }
.hero .pace { margin-top: 12px; font-size: clamp(14px, 1.6vw, 19px); color: var(--dim); }
.hero .pace b { color: var(--ink); font-weight: 620; }
.hero .pace .net { color: var(--accent); }
.hero .pace .net.red { color: var(--warn); }
.spark { display: block; width: 100%; height: clamp(34px, 4.6vw, 54px); margin-top: 16px; }
.spark rect { fill: var(--bar); }
.spark rect.on { fill: var(--accent); opacity: 0.75; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: clamp(12px, 1.6vw, 20px); align-items: start; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: clamp(12px, 1.6vw, 20px); align-items: start; }
.day .label { color: var(--dim); font-size: clamp(13px, 1.3vw, 15px); }
.day .label small { color: var(--faint); }
.day .figure { font-size: clamp(24px, 3.2vw, 38px); font-weight: 620; letter-spacing: -0.02em; margin-top: 4px; }
.day .figure.quiet { color: var(--faint); font-weight: 560; font-size: clamp(17px, 2vw, 22px); }
h2 { margin: 0 0 14px; font-size: clamp(12px, 1.2vw, 14px); font-weight: 600; color: var(--dim); text-transform: lowercase; letter-spacing: 0.02em; }
.rows { display: grid; gap: 11px; }
.row { display: grid; grid-template-columns: 1fr auto; align-items: baseline; gap: 12px; font-size: clamp(14px, 1.5vw, 17px); }
.row .amount { font-weight: 600; }
.row .when { color: var(--faint); font-size: 0.85em; }
.track { grid-column: 1 / -1; height: 6px; border-radius: 99px; background: var(--bar); overflow: hidden; margin-top: -4px; }
.track span { display: block; height: 100%; min-width: 4px; border-radius: 99px; background: var(--accent); opacity: 0.8; }
.none { color: var(--faint); }
.flag { color: var(--warn); font-size: clamp(12px, 1.2vw, 14px); }
.empty { text-align: center; padding: clamp(30px, 8vw, 90px) 0; }
.empty .figure { font-size: clamp(22px, 3vw, 34px); font-weight: 620; }
.empty .hint { color: var(--dim); margin-top: 10px; font-size: clamp(14px, 1.5vw, 17px); }
"""


def _esc(value) -> str:
    """Everything dynamic goes through here.

    Merchant labels and category names arrive from imported statements, and
    SOUL.md is explicit that imported text is untrusted. In a chat message a
    payee named `<b>` is a curiosity; in a browser it is markup, and
    `<script>` is code running in a page that reads the owner's ledger.
    """
    return html.escape(str(value), quote=True)


def _spark(days: list[dict], today_index: int, total_days: int) -> str:
    """One bar per day of the month, the current day highlighted.

    Bars, not a line: a line implies a continuous quantity between the
    points, and there is no such thing as spending "between" two days. The
    scale is the month's own busiest day, so the shape answers "was today
    heavy?" rather than pretending to an absolute scale nobody has.

    The slots are the WHOLE month even though only elapsed days are drawn.
    Three days stretched across the full width is a month that looks spent;
    three bars against twenty-seven empty ones is a month that has barely
    started, which is what it is -- and it is the same reading the projection
    refuses to make up when `basis.usable` is false.
    """
    if not days:
        return ""
    peak = max((d["expense"] for d in days), default=0) or 1
    n = max(len(days), total_days)
    slot = 100 / n
    bars = []
    for i, d in enumerate(days):
        h = max(2.0, 100 * d["expense"] / peak)
        cls = ' class="on"' if i == today_index else ""
        bars.append(
            f'<rect{cls} x="{i * slot + slot * 0.12:.3f}" y="{100 - h:.3f}" '
            f'width="{slot * 0.76:.3f}" height="{h:.3f}" rx="0.6">'
            f'<title>{_esc(d["day"])} · {_esc(d["expense_fmt"])}</title></rect>')
    return (f'<svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none"'
            f' role="img" aria-hidden="true">{"".join(bars)}</svg>')


def _ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th -- and 11th, 12th, 13th, which are the ones that
    catch a naive rule out."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _when(due: dict, words: dict) -> str:
    if due["days_away"] == 0:
        return words["due_today"]
    if due["days_away"] == 1:
        return words["tomorrow"]
    day = due["due_day"]
    return words["day_of_month"] % (_ordinal(day) if words.get("ordinal_days")
                                    else day)


def render(snap: dict) -> str:
    """The snapshot as one HTML document."""
    w = snap["words"]
    title = f'{snap["month_label"]} · cfo'

    head = (
        "<!-- Written by cfo-shared/scripts/panel.py from the ledger. No model"
        " wrote any figure on this page. -->\n"
        '<!doctype html>\n<html lang="%s">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        # A refresh, not a websocket and not a poll: the page has no server to
        # ask. Every ledger write rewrites the file underneath an open tab, so
        # a minute is the longest a propped-up tablet can be stale.
        '<meta http-equiv="refresh" content="60">\n'
        '<meta name="referrer" content="no-referrer">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n"
    ) % (_esc(snap["language"]), _esc(title), STYLE)

    stamp = (f'<div class="stamp">{_esc(w["updated"] % snap["updated"])}'
             f' · {_esc(snap["zone"])}</div>')
    header = (f'<header><h1>{_esc(snap["month_label"])} '
              f'<small>{_esc(snap["year"])}</small></h1>{stamp}</header>')

    if snap["is_empty"]:
        body = (f'<section class="card empty"><div class="figure">'
                f'{_esc(w["empty"])}</div>'
                f'<div class="hint">{_esc(w["empty_hint"])}</div></section>')
        return (head + f'<div class="wrap">{header}{body}</div>\n'
                "</body>\n</html>\n")

    # --- hero: what the month has cost, and where it lands -----------------
    if snap["projection"]:
        p = snap["projection"]
        net_class = "net red" if p["in_the_red"] else "net"
        net_word = w["in_the_red"] if p["in_the_red"] else w["left_over"]
        pace = (f'<div class="pace">{_esc(w["at_this_pace"])} '
                f'<b>{_esc(p["expense_fmt"])}</b> · '
                f'<span class="{net_class}">{_esc(p["net_fmt"])} '
                f'{_esc(net_word)}</span></div>')
    else:
        # No projection, deliberately. See the module docstring and
        # money.basis(): a pace over two days is one purchase times thirty,
        # and on a wall it would sit there all day in 72px type.
        pace = (f'<div class="pace">{_esc(w["too_young"])} · '
                f'{_esc(w["certain_instead"] % snap["fixed_expense_fmt"])}</div>')

    flag = (f'<div class="flag">{_esc(w["demo"])}</div>'
            if snap["is_demo"] else "")
    hero = (f'<section class="card hero">{flag}'
            f'<div class="label">{_esc(w["spent_so_far"])}</div>'
            f'<div class="figure">{_esc(snap["spent_fmt"])}</div>'
            f'{pace}{_spark(snap["days"], snap["elapsed_days"] - 1, snap["total_days"])}'
            f'</section>')

    # --- today and yesterday ----------------------------------------------
    def day_card(data: dict, label: str, suffix: str = "") -> str:
        if data["count"]:
            figure = (f'<div class="figure">{_esc(data["expense_fmt"])}</div>')
        else:
            figure = (f'<div class="figure quiet">'
                      f'{_esc(w["nothing_logged"])}</div>')
        tail = f' <small>{_esc(suffix)}</small>' if suffix and data["count"] else ""
        return (f'<section class="card day"><div class="label">{_esc(label)}'
                f'{tail}</div>{figure}</section>')

    pair = (f'<div class="pair">'
            f'{day_card(snap["today"], w["today"], w["so_far"])}'
            f'{day_card(snap["yesterday"], w["yesterday"])}</div>')

    # --- categories --------------------------------------------------------
    cat_rows = "".join(
        f'<div class="row"><span>{_esc(c["label"])}</span>'
        f'<span class="amount">{_esc(c["total_fmt"])}</span>'
        f'<span class="track"><span style="width:{c["share"]}%"></span></span>'
        f'</div>'
        for c in snap["categories"])
    cats = (f'<section class="card"><h2>{_esc(w["categories"])}</h2>'
            f'<div class="rows">{cat_rows}</div></section>')

    # --- what falls due ----------------------------------------------------
    if snap["upcoming"]:
        due_rows = "".join(
            f'<div class="row"><span>{_esc(d["label"])} '
            f'<span class="when">{_esc(_when(d, w))}</span></span>'
            f'<span class="amount">{_esc(d["amount_fmt"])}</span></div>'
            for d in snap["upcoming"])
    else:
        due_rows = (f'<div class="none">'
                    f'{_esc(w["nothing_due"] % UPCOMING_DAYS)}</div>')
    upcoming = (f'<section class="card"><h2>'
                f'{_esc(w["upcoming"] % UPCOMING_DAYS)}</h2>'
                f'<div class="rows">{due_rows}</div></section>')

    return (head + f'<div class="wrap">{header}{hero}{pair}'
            f'<div class="cols">{cats}{upcoming}</div></div>\n'
            "</body>\n</html>\n")


# --------------------------------------------------------------------------
# write: atomically, or not at all
# --------------------------------------------------------------------------

def write(con=None, path=None, today=None) -> Path:
    """Render the panel and replace the file in one step.

    Written to a sibling temp file and renamed, because the reader is a
    browser reloading on a timer with no way to know it caught the file
    mid-write. A half-written page is a blank screen on a wall, and the next
    refresh is a minute away.
    """
    con = con or money.connect()
    target = Path(path) if path else panel_path(con)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(render(snapshot(con, today=today)), encoding="utf-8")
    tmp.replace(target)
    return target


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="panel", description="render the ledger as a wall panel")
    p.add_argument("--path", default=None,
                   help="write here instead of $CFO_DATA/panel/index.html")
    p.add_argument("--json", action="store_true",
                   help="print the panel's figures as JSON instead of writing")
    p.add_argument("--stdout", action="store_true",
                   help="print the HTML instead of writing it")
    args = p.parse_args(argv)

    con = money.connect()
    if args.json:
        snap = {k: v for k, v in snapshot(con).items() if k != "words"}
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    if args.stdout:
        print(render(snapshot(con)), end="")
        return 0

    target = write(con, args.path)
    # STDOUT STAYS EMPTY ON SUCCESS. This file is a `--no-agent` cron script,
    # and hermes delivers such a script's stdout verbatim: "Empty stdout =
    # silent". A cheerful confirmation here would be a notification every ten
    # minutes, forever, which is how an agent gets muted. The path goes to
    # stderr, where a person running it by hand still sees it.
    print(f"panel written to {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(money._run(main, None))
