#!/usr/bin/env python3
"""Build the iPhone shortcuts that feed the agent without typing.

Two shortcuts, one job each, both sending one iMessage to the agent:

  cfo-apple-pay   run by a Wallet/Transaction automation: every card tap
                  becomes `💳 <amount> · <merchant> · <card>`
  cfo-bank-sms    run by a Message automation on the bank's SMS sender:
                  the alert text is forwarded as `📩 <text>`

Why a file and not a screenshot walkthrough: a shortcut is a plist, and the
part people get wrong by hand -- wiring the trigger's Amount / Merchant /
Card fields into the text, turning "Show When Run" off so nothing pops up at
the till -- is exactly the part a file carries already. What CANNOT be
shipped is the automation itself: iOS refuses to share or install a trigger
from a link, so the owner still creates it, in one step, as "Run Shortcut".

Output is unsigned. iOS 15+ refuses to import an unsigned shortcut file, so
the two `*.shortcut` files committed beside this script were signed on a Mac
with `shortcuts sign --mode anyone`, which anyone can re-run:

    python3 docs/shortcuts/build.py /tmp/unsigned
    shortcuts sign --mode anyone -i /tmp/unsigned/cfo-apple-pay.shortcut \
        -o docs/shortcuts/cfo-apple-pay.shortcut

The unsigned file MUST be named `.shortcut` too: `shortcuts sign` keys off
the extension and answers "isn't in the correct format" to a `.plist`.

The recipient is an IMPORT QUESTION: on install, Shortcuts asks which number
the owner texts their agent at, once, and writes it into the Send Message
action. No number is baked in -- the number is the owner's own thread with
Plow, and this repo carries nobody's.

The text ranges are UTF-16 offsets (NSRange), which is why the emoji is
counted as two.
"""

from __future__ import annotations

import plistlib
import sys
import uuid
from pathlib import Path

OBJ = "￼"  # the object-replacement character Shortcuts uses as a slot


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def token_string(parts: list) -> dict:
    """A Shortcuts rich string from a list of str | attachment dicts."""
    text, attachments = "", {}
    for part in parts:
        if isinstance(part, str):
            text += part
        else:
            attachments[f"{{{utf16_len(text)}, 1}}"] = part
            text += OBJ
    return {"Value": {"string": text, "attachmentsByRange": attachments},
            "WFSerializationType": "WFTextTokenString"}


def input_property(name: str) -> dict:
    """The trigger's input, narrowed to one field (Amount, Merchant, Card)."""
    return {"Type": "ExtensionInput",
            "Aggrandizements": [{"Type": "WFPropertyVariableAggrandizement",
                                 "PropertyName": name}]}


def shortcut_input() -> dict:
    return {"Type": "ExtensionInput"}


def text_action(parts: list, uid: str) -> dict:
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {"UUID": uid,
                                           "WFTextActionText": token_string(parts)}}


def send_message_action(text_uid: str) -> dict:
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.sendmessage",
            "WFWorkflowActionParameters": {
                "ShowWhenRun": False,
                "WFSendMessageContent": token_string([
                    {"Type": "ActionOutput", "OutputName": "Text",
                     "OutputUUID": text_uid}]),
                # Filled by the import question below.
                "WFSendMessageActionRecipients": {
                    "Value": {"WFContactFieldValues": []},
                    "WFSerializationType": "WFContactFieldValue"},
            }}


def workflow(actions: list, glyph: int, color: int) -> dict:
    return {
        "WFWorkflowClientVersion": "2607.0.3",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": color,
                           "WFWorkflowIconGlyphNumber": glyph},
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowInputContentItemClasses": [
            "WFAppStoreAppContentItem", "WFArticleContentItem",
            "WFContactContentItem", "WFDateContentItem",
            "WFEmailAddressContentItem", "WFGenericFileContentItem",
            "WFImageContentItem", "WFiTunesProductContentItem",
            "WFLocationContentItem", "WFDCMapsLinkContentItem",
            "WFAVAssetContentItem", "WFPDFContentItem",
            "WFPhoneNumberContentItem", "WFRichTextContentItem",
            "WFSafariWebPageContentItem", "WFStringContentItem",
            "WFURLContentItem"],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
        "WFQuickActionSurfaces": [],
        "WFWorkflowImportQuestions": [{
            "ActionIndex": 1,
            "Category": "Parameter",
            "ParameterKey": "WFSendMessageActionRecipients",
            "DefaultValue": "",
            "Text": "Which number do you text your cfo at? (the one you "
                    "sent the activation code to)",
        }],
        "WFWorkflowActions": actions,
    }


def apple_pay() -> dict:
    uid = str(uuid.uuid4()).upper()
    return workflow([
        text_action(["💳 ", input_property("Amount"), " · ",
                     input_property("Merchant"), " · ",
                     input_property("Card")], uid),
        send_message_action(uid),
    ], glyph=59680, color=4292093695)


def bank_sms() -> dict:
    uid = str(uuid.uuid4()).upper()
    return workflow([
        text_action(["📩 ", shortcut_input()], uid),
        send_message_action(uid),
    ], glyph=59511, color=4282601983)


def main(argv=None) -> int:
    out = Path(argv[0]) if argv else Path("/tmp")
    out.mkdir(parents=True, exist_ok=True)
    for name, build in (("cfo-apple-pay", apple_pay), ("cfo-bank-sms", bank_sms)):
        path = out / f"{name}.shortcut"
        with path.open("wb") as fh:
            plistlib.dump(build(), fh, fmt=plistlib.FMT_XML, sort_keys=True)
        print(f"wrote {path}")
    print("now: shortcuts sign --mode anyone -i <file> -o docs/shortcuts/<name>.shortcut")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
