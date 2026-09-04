# The builder name, and why this agent shows as "Anonymous Builder"

**Status as of 2026-09-04:** unresolved, and not resolvable from here. Waiting
to see whether upstream notices before launch; if not, raise it with the Index
maintainer directly.

The `cfo` page on the Agent Index is complete except for one thing: where it
should say who built it, it says *"built by an anonymous builder"*. The blurb,
repo, runtime and screenshot are all correct.

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

## Why it cannot be fixed from here

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

## The message to send

English, for an issue or a note to the maintainers:

> The Agent Index identity change on 2026-09-03 leaves new builders with no way
> to appear under their own name. The page resolves the builder through
> `GET /v1/auth/index-profiles/{owner_uid}`, but `PATCH /v1/auth/profile`
> requires the `*:*` scope and returns 403 for every credential an owner holds:
> the container's `PLOW_AGENT_TOKEN`, `DOMO_MCP_TOKEN`, `PLOW_CHAT_TOKEN`, a
> freshly minted OTP token from `/v1/auth/otp/verify`, and the Plow assistant
> itself when asked in chat. There is no web console, and the client's README
> documents no alternative. Accounts from the GitHub device-flow era have a
> name and avatar; accounts created by SMS activation have `display_name: null`
> and show as "Anonymous Builder" with no way out. How is the profile meant to
> be filled in?

Portuguese, for a direct message:

> A mudança de identidade do Agent Index de 03/09 deixou builders novos sem
> como aparecer com nome. A página resolve o builder via
> `GET /v1/auth/index-profiles/{owner_uid}`, mas `PATCH /v1/auth/profile` exige
> escopo `*:*` e retorna 403 para todas as credenciais que um dono possui: o
> `PLOW_AGENT_TOKEN` do container, o `DOMO_MCP_TOKEN`, o `PLOW_CHAT_TOKEN`, um
> token recém-obtido por OTP (`/v1/auth/otp/verify`) e o próprio assistente
> Plow. Não há console web e o README do client não documenta alternativa.
> Contas anteriores ao device flow do GitHub têm nome e avatar; as criadas por
> ativação SMS têm `display_name: null` e ficam permanentemente como "Anonymous
> Builder". Como preencher o perfil?

## If it gets fixed

Nothing here needs redeploying. The index resolves the profile per request, so
the page picks up a name the moment Plow holds one — no re-registration, no
`agent-mgr deploy`. Check it with:

```sh
curl -s "https://agent-index-server.vercel.app/v1/agents?agent_id=cfo" \
  | python3 -c "import json,sys; [print(a['agent_id'], a['builder']) for a in json.load(sys.stdin)['agents']]"
```

Note that the `agent_id` query parameter is currently ignored — the server
returns every agent, so filter client-side rather than taking `agents[0]`.
