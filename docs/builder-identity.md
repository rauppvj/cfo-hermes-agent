# The builder name, and why this agent showed as "Anonymous Builder"

**Status: RESOLVED on 2026-09-09.** Upstream opened the endpoint. The name is
set and the page renders it; the rest of this file is kept as the account of
what was actually wrong, because the shape of it recurs.

## How it was fixed

`PATCH /v1/auth/profile` now accepts the container's own `PLOW_AGENT_TOKEN` —
the same credential the table below records as 403. Nothing here changed; the
scope did.

```sh
TOK=$(grep '^PLOW_AGENT_TOKEN=' ~/.hermes-cfo/.env | cut -d= -f2-)
curl -X PATCH -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
     -d '{"display_name":"Vinicius"}' https://api.plow.co/v1/auth/profile
```

`GET` the same path first to see what Plow holds. There is also a CLI for it in
[`plow-pbc/plow-agents`](https://github.com/plow-pbc/plow-agents) — `plow-agents
profile --name … --photo …`, added 2026-09-04 — which takes a **local file** for
the photo and uploads it. That one needs an account token from `plow-agents
login`, texted from the owner's phone; the curl above needs nothing new.

No redeploy and no re-registration: the index resolves the profile per request,
so the page picked the name up in seconds.

---

## What it was (kept, 2026-09-04)

The `cfo` page on the Agent Index was complete except for one thing: where it
should say who built it, it said *"built by an anonymous builder"*. The blurb,
repo, runtime and screenshot were all correct.

## What changed

Until the morning of 2026-09-03, the builder shown on an agent's page was a
value **this repo sent and the index stored**:

```sh
agent_index_client.py --register --agent cfo \
  --builder-name "Vinicius Raupp" --builder-handle @rauppvj ...
```

That worked, and the name was visible on the page.

Upstream then replaced the identity model twice in one day (see
[`usage_report.sh`](../cfo-shared/scripts/usage_report.sh) for the credential
side of the same churn). The relevant half:

- `--builder-name` and `--builder-handle` **no longer exist**. The current
  client rejects them as unknown options before sending anything, and its
  `register()` no longer has the fields in the request body.
- The page now resolves the builder **live**, per request, by asking Plow:
  `GET /v1/auth/index-profiles/{owner_uid}`. Nothing is stored on the index.

The site's own source states the rule:

> What shows is the name and photo Plow holds for them, which the index
> resolves per request rather than storing — and an anonymous builder when
> Plow has no profile.

So the name did not get lost or corrupted. **Its source was replaced**, from a
field this repo filled in to a profile that was never filled in.

## Why it could not be fixed from here, until it could

The Plow profile is written with `PATCH /v1/auth/profile`
(`{"display_name": ..., "photo_url": ...}`). That endpoint requires the `*:*`
scope, and every credential an agent owner can obtain is refused:

| Credential | Where it comes from | `PATCH /v1/auth/profile` |
| --- | --- | --- |
| `PLOW_AGENT_TOKEN` | the container, loaded from the home's `.env` | 403 `*:*` |
| `DOMO_MCP_TOKEN` | the home's `.env` | 403 `*:*` |
| `PLOW_CHAT_TOKEN` | the home's `.env` | 403 `*:*` |
| OTP token | `POST /v1/auth/otp/verify`, owner's own phone | 403 `*:*` |
| **the Plow assistant itself** | asked in chat, it ran `curl` | **403** |

There is also no web surface: `plow.co` is a landing page, and `/settings`,
`/profile` and `/account` are all 404. The upstream client's README does not
mention profiles at all — the word does not appear in it.

This account exists and is healthy; it was created during activation on
2026-09-01 and is keyed to a phone number, with no name ever entered:

```json
"owner_identity": { "display_name": null, "phones": ["+55..."], "emails": [] }
```

`profile_unavailable: false` on the index confirms the lookup **succeeds** and
simply returns a profile with no name — this is an empty field, not a broken
resolution.

## Who it affects

Not just this agent. The one builder on the index who *does* show a name has
`name: "Daniel"` and `photo: https://github.com/delattre1.png` — a short first
name and a GitHub avatar, which is the shape of a GitHub OAuth profile. That
is the device flow the client carried until it was removed on 2026-09-03.

The likely rule, then: **profiles populated during the GitHub era kept their
name; accounts created by SMS activation after it have no way to get one.**
Every builder who joins from now on is permanently anonymous on the page the
hackathon is judged on.

## The message that was drafted, and never needed sending

Two drafts lived here — one English, one Portuguese — asking the maintainers how
a builder was meant to fill in a profile at all. They are cut now that the
answer is "the same token you already had". Kept only as the lesson: the gap was
real and was closed upstream within five days, and the useful move while it was
open was to write down exactly which credentials had been tried and what each
one returned, rather than to guess at a workaround.

## Checking it

Nothing here needs redeploying. The index resolves the profile per request, so
the page picks up a name the moment Plow holds one — no re-registration, no
`agent-mgr deploy`. Check it with:

```sh
curl -s "https://agent-index-server.vercel.app/v1/agents?agent_id=cfo" \
  | python3 -c "import json,sys; [print(a['agent_id'], a['builder']) for a in json.load(sys.stdin)['agents']]"
```

Note that the `agent_id` query parameter is currently ignored — the server
returns every agent, so filter client-side rather than taking `agents[0]`.
