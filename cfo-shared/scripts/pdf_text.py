#!/usr/bin/env python3
"""Turn a PDF statement into text, inside the container.

Run under uv so the dependency is fetched on demand rather than baked into an
image the owner would have to rebuild:

    uv run --quiet --with pypdf python3 pdf_text.py <file.pdf>

The password, when there is one, arrives in CFO_PDF_PASSWORD -- never in argv,
which any process on the host can read out of the process table. It is used
once, here, and is never written to disk or into the ledger.

Exit codes are the interface: 2 means "locked, ask for the password", which is
a question for a person rather than an error to report.
"""

import json
import os
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: pdf_text.py <file.pdf>"}))
        return 1
    path = sys.argv[1]

    try:
        import pypdf
    except ImportError:
        print(json.dumps({
            "error": "pypdf is not available; run this through "
                     "`uv run --with pypdf`",
        }))
        return 1

    try:
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            password = os.environ.get("CFO_PDF_PASSWORD", "")
            if not password:
                print(json.dumps({
                    "error": "this PDF is password-protected",
                    "needs_password": True,
                    "ask": "ask the owner for the password -- for a bank "
                           "statement it is commonly their CPF/SSN, a birth "
                           "date, or the first digits of the account",
                }))
                return 2
            if reader.decrypt(password) == 0:
                print(json.dumps({
                    "error": "that password did not open the PDF",
                    "needs_password": True,
                }))
                return 2

        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as exc:                     # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1

    text = "\n".join(pages)
    if not text.strip():
        print(json.dumps({
            "error": "no text layer in this PDF -- it is probably a scan",
            "ask": "ask the bank's CSV or OFX export instead; OCR is not "
                   "worth it for a statement that exists as data elsewhere",
        }))
        return 1

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
