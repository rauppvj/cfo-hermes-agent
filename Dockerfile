# cfo, as an image: the Plow base plus this agent's persona and skills.
#
# This is the whole install. `plow-agents mint <line>` writes a credential
# beside this file and `docker compose up --build -d` boots it -- no deployer to
# clone, no GitHub CLI to sign into, no registry row, and no one-time
# activation that binds a handset and cannot be repeated. Every one of those
# was a step this repo used to require, and each was a place an install stopped:
# measured on the Agent Index, one install in three completed.
#
# The tag is an immutable `base-<sha>` naming one commit of plow-pbc/plow-hermes-agent,
# with its digest beside it. Pinned both ways on purpose: the tag says WHICH
# commit a reader can go and read, the digest is what Docker actually verifies.
# It is never moved -- the base carries the chat plugin, the boot contract and
# the base persona, so a floating tag would change who this agent is between
# one install and the next.
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-8710797b6409c77df560c6198407765d138ea617@sha256:b9627febe57e34ec0df373709ad91a27a7fda68093e76d519678cac1012614f9

# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
# plow-init writes $HERMES_HOME/SOUL.md on EVERY boot as the base persona
# followed by this file. Nothing is copied to that path: it is overwritten at
# boot, and a populated home volume would otherwise shadow a newer image
# forever -- which is the failure this repo hit under the old deployer, where
# SOUL.md was a single-file bind mount and a `git pull` without a restart left
# the agent running yesterday's identity with no sign that it had.
#
# This file says only what is specific to cfo. The voice, the no-fabrication
# rule, the untrusted-content rule and the group-chat disclosure now come from
# the base and from the chat plugin, so anything restated here is a second copy
# that can disagree with the one that ships.
COPY runtime/persona.md /opt/hermes/plow-seed/persona.md
COPY LICENSE /usr/share/doc/cfo/
RUN chmod 0644 /opt/hermes/plow-seed/persona.md

# --------------------------------------------------------------------------
# The skills
# --------------------------------------------------------------------------
# Shipped at /opt/hermes/skills, outside every home, and reconciled into
# whichever home this container gets by the base runtime. That is what makes an
# image update reach a skill the agent has not customised, and what keeps the
# eight of them out of the one directory a populated volume shadows.
#
# FLAT, one per line, each landing directly under skills/. Every SKILL.md here
# names its scripts at $HERMES_HOME/skills/cfo-shared/scripts/..., so the
# bundles only resolve as siblings directly under that directory -- and a
# single directory copied AT skills/ would shadow the seed skills the base
# bundles (plow-invite, google-workspace, owners-mac).
COPY cfo-shared/    /opt/hermes/skills/cfo-shared/
COPY cfo-log/       /opt/hermes/skills/cfo-log/
COPY cfo-ask/       /opt/hermes/skills/cfo-ask/
COPY cfo-brief/     /opt/hermes/skills/cfo-brief/
COPY cfo-import/    /opt/hermes/skills/cfo-import/
COPY cfo-panel/     /opt/hermes/skills/cfo-panel/
COPY cfo-setup/     /opt/hermes/skills/cfo-setup/
COPY cfo-simulate/  /opt/hermes/skills/cfo-simulate/

# Normalize whatever modes the checkout carried, preserving the executable bit.
# -mindepth 1: the skills root itself is the base's, root-owned and sticky, and
# recursing over it would reset that mode and leave the directory unwritable
# for the gateway's own bundled-skill install, which then scans nothing.
RUN find /opt/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} +

# --------------------------------------------------------------------------
# The unattended copies: root-owned, outside the agent's reach
# --------------------------------------------------------------------------
# Everything a supervisor or a cron row runs on a timer, in a copy no turn can
# rewrite. The home's copy under skills/ belongs to uid 10000 in a running
# container -- the runtime chowns what it seeds -- so scheduling that one would
# turn a single prompt-injected edit into code that runs unattended, forever,
# holding this agent's credential and the owner's ledger. The agent still reads
# and runs the home copy by hand during a turn: that is the skill. Only the
# SCHEDULE points here.
COPY cfo-shared/scripts/ /opt/plow/cfo-shared/scripts/
RUN chown -R root:root /opt/plow/cfo-shared \
 && find /opt/plow/cfo-shared -type d -exec chmod 0755 {} + \
 && find /opt/plow/cfo-shared -type f -exec chmod 0644 {} +

# The usage reporter, fetched at build from the commit vendor/client.pin names
# and checked against the hash beside it. Fetched rather than committed because
# plow-pbc/agent-index-client owns that file; pinned rather than tracked from a
# branch because this runs inside an agent holding a live credential.
COPY vendor/client.pin /opt/plow/agent-index-client.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/agent-index-client.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/agent-index-client.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/agent-index-client.pin)"; \
    curl -fsS --max-time 60 -o /opt/plow/agent-index-client.py \
      "https://raw.githubusercontent.com/plow-pbc/agent-index-client/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/agent-index-client.py | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "agent-index client is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/agent-index-client.py

# --------------------------------------------------------------------------
# The services and the directories they write into
# --------------------------------------------------------------------------
COPY image/s6-overlay/ /etc/s6-overlay/
COPY image/cont-init.d/10-cfo-dirs /etc/cont-init.d/10-cfo-dirs
RUN chmod 0755 /etc/cont-init.d/10-cfo-dirs \
      /etc/s6-overlay/s6-rc.d/agent-index/run \
      /etc/s6-overlay/s6-rc.d/cfo-panel/run \
      /etc/s6-overlay/s6-rc.d/cfo-schedule/run

# The ledger's directory, created empty so first boot has somewhere to write
# before the owner has said anything. 0700 uid 10000: the ledger is one
# person's money and nothing else in this container has a reason to read it.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/cfo

# Outside /var/lib, and that is the whole reason for the path. Hermes refuses
# to deliver a model-emitted MEDIA: path under its denylist -- /etc /proc /sys
# /dev /root /boot /var/log /var/lib /var/run -- and this contract's entire
# HERMES_HOME is /var/lib/hermes. A CSV written beside the ledger is dropped
# with "Skipping unsafe MEDIA directive path" in a log nobody opens: the owner
# asks for their spending, the agent says it sent the file, and no file
# arrives. So exports and the panel are written here instead.
#
# Not on the home volume, deliberately: both are derived from the ledger and
# rewritten on demand, and an export that outlived the container would be a
# copy of the owner's spending nobody asked to keep.
RUN install -d -o 10000 -g 10000 -m 0755 /srv/cfo \
 && install -d -o 10000 -g 10000 -m 0755 /srv/cfo/export \
 && install -d -o 10000 -g 10000 -m 0755 /srv/cfo/panel \
 && install -d -o 10000 -g 10000 -m 0755 /srv/cfo/inbox

# Where the export lands, read by money.py. Named here rather than defaulted in
# the script so the reason above lives next to the path it explains.
ENV CFO_EXPORT=/srv/cfo/export
ENV CFO_PANEL=/srv/cfo/panel/index.html
ENV CFO_INBOX=/srv/cfo/inbox/notifications.jsonl

# The cron registrar, root-owned beside the other unattended code.
COPY image/scripts/cfo-schedule.py /opt/plow/cfo-schedule.py
RUN chmod 0644 /opt/plow/cfo-schedule.py
