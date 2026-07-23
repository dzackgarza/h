#!/usr/bin/env python3
"""Record real Mathpix answers for the images the suite sends, one call per image.

The tests run against `tests/common/mathpix.py`, which replays recordings rather than
calling the paid API. When a test sends an image nothing has been recorded for, the
emulator writes it to `tests/corpus/mathpix/unrecorded/` and fails the run. This turns
those into fixtures: it sends each one to the real API exactly once and stores the
answer verbatim beside the image it answers.

    MATHPIX_API_KEY=... python bin/record_mathpix_fixtures.py

Verbatim matters. A fixture edited by hand is a guess about what Mathpix returns, which
is the thing the emulator exists to stop the suite from doing.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from tests.common.mathpix import CORPUS, PNG_DATA_URI, UNRECORDED, digest

ENDPOINT = "https://api.mathpix.com/v3/text"


def record(png: bytes, key: str) -> dict:
    """One real call, with the payload `ocr_latex` sends."""
    response = requests.post(
        ENDPOINT,
        headers={"app_key": key},
        json={
            "src": PNG_DATA_URI + base64.b64encode(png).decode(),
            "formats": ["text"],
            "math_inline_delimiters": ["$", "$"],
            "math_display_delimiters": ["$$", "$$"],
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    key = os.environ.get("MATHPIX_API_KEY", "").strip()
    if not key:
        sys.stdout.write("MATHPIX_API_KEY is not set; recording needs the real API.\n")
        return 1

    pending = sorted(UNRECORDED.glob("*.png")) if UNRECORDED.exists() else []
    if not pending:
        sys.stdout.write(
            f"Nothing to record: {UNRECORDED} is empty. Run the suite first -- it puts "
            "images it has no recording for there.\n"
        )
        return 0

    CORPUS.mkdir(parents=True, exist_ok=True)
    for image in pending:
        png = image.read_bytes()
        sha = digest(png)
        if sha != image.stem:
            sys.stdout.write(f"{image.name}: contents do not match its name; skipped\n")
            continue

        answer = record(png, key)
        (CORPUS / f"{sha}.json").write_text(
            json.dumps(answer, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (CORPUS / f"{sha}.png").write_bytes(png)
        image.unlink()
        sys.stdout.write(f"{sha}  {answer.get('text', '')[:70]!r}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
