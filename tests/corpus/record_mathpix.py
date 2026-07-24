"""Record Mathpix's answer for each crop the PDF annotation walkthrough produces.

The walkthrough in ``tests/functional/api/pdf_math_annotations_test.py`` drives the real
create path, which renders a region of the fixture paper and OCRs it. OCR is the one step
that cannot run in the suite -- it is a paid call to a third party -- so its answers are
recorded here, keyed by the bytes of the crop that produced them. A crop the recording
does not know about fails the walkthrough loudly rather than passing on a stale answer.

Run it with a live ``MATHPIX_API_KEY`` after changing the selections or anything that
moves the crop:

    just _record-pdf-math-fixtures
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import sys

import pymupdf as fitz
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parents[2]))

from h.services import pdf_math
from tests.functional.api.pdf_math_annotations_test import (
    PAPER,
    RECORDINGS,
    SELECTIONS,
)


def main() -> None:
    RECORDINGS.parent.mkdir(parents=True, exist_ok=True)
    key = pdf_math.os.environ["MATHPIX_API_KEY"]
    recordings = {}
    document = fitz.open(PAPER)
    for name, (page_index, exact) in SELECTIONS.items():
        png = pdf_math._selection_png(document[page_index], exact)  # noqa: SLF001
        if png is None:
            msg = f"{name}: the selection is not locatable on page {page_index}"
            raise SystemExit(msg)
        response = requests.post(
            "https://api.mathpix.com/v3/text",
            headers={"app_key": key},
            json={
                "src": "data:image/png;base64," + base64.b64encode(png).decode(),
                "formats": ["text"],
                "math_inline_delimiters": ["$", "$"],
                "math_display_delimiters": ["$$", "$$"],
            },
            timeout=60,
        )
        response.raise_for_status()
        recordings[hashlib.sha256(png).hexdigest()] = {
            "selection": name,
            "body": response.json(),
        }
        print(f"{name}: {response.json().get('text')!r}")
    RECORDINGS.write_text(json.dumps(recordings, indent=1, sort_keys=True) + "\n")
    print(f"\n{len(recordings)} crops recorded to {RECORDINGS}")


main()
