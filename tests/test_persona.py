"""The identity, under both contracts, and the rules it must never lose.

Two files say who this agent is, because two contracts read a different one:

  * `runtime/persona.md` -- the current one. `plow-init` writes
    $HERMES_HOME/SOUL.md on every boot as the BASE persona followed by this
    file, so it says only what is specific to cfo.
  * `runtime/SOUL.md` -- the whole identity as one file, bind-mounted over the
    image's by the deprecated deployer. Frozen, and still in use by one live
    instance.

They will drift the day somebody edits one of them. What must survive that is
not the wording but the rules, and each of these has cost a real defect:

  * arithmetic in the model instead of in the engine: a projection of
    -R$ 34,000 from four days filed on one, said in the same confident voice
    as every correct number;
  * a brief in Portuguese to an owner who had written nothing but English for
    a week, because every worked example in the skills is Portuguese;
  * income minus expenses answered as a balance;
  * a raw traceback sent to a phone as the answer to a question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
PERSONA = RUNTIME / "persona.md"
SOUL = RUNTIME / "SOUL.md"

# One phrase per rule, chosen to be the part that cannot be paraphrased away
# without losing the rule itself.
RULES = {
    "no arithmetic in the model": "You do not do arithmetic",
    "the engine is the source of every figure": "money.py",
    "mirror the owner's language": "reply in that one",
    "the Portuguese examples are one owner's, not a house style":
        "not a house style",
    "spending is not the balance": "Income minus expenses is not a balance",
    "never paste a traceback at the owner": "never paste its raw output",
    "say 'at this pace', not 'you will spend'": "at this pace",
    "report, never lecture": "Report, never lecture",
}


def flat(text: str) -> str:
    """One line, single-spaced -- these files are hard-wrapped at 76 columns,
    so a rule's own sentence is usually split across two of them."""
    return " ".join(text.split())


@pytest.mark.parametrize("identity", [PERSONA, SOUL], ids=["persona.md", "SOUL.md"])
@pytest.mark.parametrize("rule,phrase", RULES.items(), ids=list(RULES))
def test_the_identity_keeps_the_rule(identity, rule, phrase):
    assert phrase in flat(identity.read_text()), f"{identity.name} lost: {rule}"


def test_the_persona_does_not_restate_what_the_base_owns():
    """A variant's persona says only what is specific to it.

    The base image's own persona carries the voice, the no-fabrication rule,
    the who-you-are-talking-to rule and -- the one this checks -- that content
    arriving inside a tool result, a file or a forwarded message is DATA and
    never an instruction. Restating it here would be a second copy that can
    disagree with the one that ships, and the base's copy reaches every
    deployed agent on its next pin bump while this one does not.
    """
    text = PERSONA.read_text()
    assert "# Safety" not in text and "## Safety" not in text
    assert "untrusted" not in text.lower(), \
        "the base persona owns the untrusted-content rule; cfo-import names " \
        "the one statement-shaped case where it bites"


def test_the_soul_is_whole_and_the_persona_is_a_supplement():
    """The legacy file has to stand alone; the current one never does."""
    assert SOUL.read_text().startswith("# Who you are")
    assert PERSONA.read_text().startswith("# cfo")
