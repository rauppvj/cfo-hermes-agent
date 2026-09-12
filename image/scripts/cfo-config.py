#!/usr/bin/env python3
"""Put this agent's two context settings into the home's config.yaml, at boot.

Why an agent that logs money has an opinion about context management.

On 2026-09-10 the owner's phone forwarded two card taps, at 18:24 and 21:40.
Neither was recorded. The first got a reply ninety-one minutes later that
loaded a skill and then stopped; the second got no turn at all. Both were
finally logged at 22:18 by the evening brief -- a different job, in a
different session. The gateway was up the whole time, the container was
healthy, and nothing anywhere said a message had been dropped.

What the log said:

    Codex stream produced no SSE events for 5443s after first byte
    (threshold 180s, model=gpt-5.6-sol, context=~173,010 tokens)

The DM session had been open since 2026-08-31 -- twelve days, 342 messages --
and measured 730KB across the store: 320KB of content, 51KB of tool calls,
and **329KB of the provider's own reasoning items**, replayed on every turn.
At ~175,000 tokens the provider answered with a first byte and then nothing,
for as long as the connection was left open.

Nothing in the defaults stops that, and each default is defensible alone:

  * `session_reset.mode` is `none` since July 2026 -- people expect a
    conversation to persist -- so the session never rotates;
  * `compression.threshold` is a RATIO of the window, and the Codex autoraise
    lifts it further for the 272K family, so full compression waits for
    ~218,000 tokens -- past where this provider stalls;
  * `compression.proactive_prune_tokens` is 0, so old tool results ride in
    history and are re-sent verbatim, forever.

Two absolute numbers fix it, and they are this agent's to choose because the
shape of its conversation is its own: short messages, JSON results the owner
never sees, and **a memory that is not the transcript**. The ledger is the
memory. Nothing said three weeks ago is load-bearing -- it is a row in
SQLite, which is why this agent can afford a bounded context where a coding
agent cannot.

  * `proactive_prune_tokens` -- above this, the deterministic no-LLM prune
    reclaims old tool output (dedup, summarize, truncate). No model call, no
    quality risk.
  * `threshold_tokens` -- an absolute floor under the ratio: compression
    triggers at whichever is lower. This is the backstop that keeps a request
    away from where the provider goes quiet.

It merges rather than writes: every other key in that file belongs to
somebody else -- the base seeded it, plow-init asserts what it owns on every
boot, and the chat plugin rewrites its own section. A key already present is
left exactly as it was, so an owner who tuned either of these keeps their
value.

Run from cont-init, before any service starts, on a file the base has already
seeded. Never fatal: a config this cannot parse is one the gateway will
answer for, and failing the boot here would take the agent down over a
setting it runs perfectly well without.
"""

from __future__ import annotations

import os
import sys

# What this file owns, and nothing else.
#
# 40,000: the prune is deterministic and cheap, so it can start early -- but
# it invalidates the provider's cached prefix from the earliest rewritten
# message forward, so not so early that it fires on an ordinary week.
#
# 90,000: a third of this provider's 272K window, and roughly half of where it
# was measured going quiet. Compression here costs one summarization call and
# keeps every request small enough to answer.
WANT = {
    "compression": {
        "proactive_prune_tokens": 40000,
        "threshold_tokens": 90000,
    },
}

CONFIG = os.environ.get("CFO_CONFIG", "/var/lib/hermes/config.yaml")


def merge(current: dict, want: dict) -> tuple[dict, list[str]]:
    """Add only the keys that are absent. Returns the config and what changed."""
    added = []
    for section, values in want.items():
        block = current.get(section)
        if not isinstance(block, dict):
            # A section that is absent, or is not a mapping at all. Replacing a
            # non-mapping is the one overwrite this does: the gateway would
            # refuse it anyway, and leaving it would keep this agent's settings
            # out forever.
            block = {} if block is None else {}
            current[section] = block
        for key, value in values.items():
            if key in block:
                continue
            block[key] = value
            added.append(f"{section}.{key}={value}")
    return current, added


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("cfo-config: no yaml module in this python -- leaving config.yaml alone",
              file=sys.stderr)
        return 0
    try:
        with open(CONFIG, encoding="utf-8") as handle:
            current = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        print(f"cfo-config: {CONFIG} does not exist yet -- nothing to merge into",
              file=sys.stderr)
        return 0
    except (OSError, yaml.YAMLError) as error:
        print(f"cfo-config: {CONFIG} is not readable as YAML ({error}) -- "
              "leaving it alone", file=sys.stderr)
        return 0
    if not isinstance(current, dict):
        print(f"cfo-config: {CONFIG} holds {type(current).__name__}, not a mapping "
              "-- leaving it alone", file=sys.stderr)
        return 0

    merged, added = merge(current, WANT)
    if not added:
        return 0

    # A sibling then a rename, the way the base writes this file: a boot
    # interrupted mid-dump must not leave a truncated config.yaml behind,
    # because the next boot keeps whatever is at that path.
    temporary = CONFIG + ".cfo-tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            yaml.safe_dump(merged, handle, sort_keys=False, allow_unicode=True)
        # The file belongs to the agent: the chat plugin rewrites it in place.
        stat = os.stat(CONFIG)
        try:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        except PermissionError:              # not root: a hand run
            pass
        os.chmod(temporary, stat.st_mode & 0o7777)
        os.replace(temporary, CONFIG)
    except OSError as error:
        print(f"cfo-config: could not write {CONFIG} ({error})", file=sys.stderr)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return 0
    print("cfo-config: " + ", ".join(added), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
