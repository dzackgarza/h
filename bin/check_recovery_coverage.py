#!/usr/bin/env python3
"""Hold the recovery modules to the coverage they have today.

The repository asks for 100% (`fail_under = 100.00` in pyproject.toml) and the test command
overrides that with `--cov-fail-under=0`. Everything except the recovery meets the real
figure; these two modules are where the fork's own code lives and where the shortfall is.

Rather than leave the gate off until they reach 100, this holds each to what it measures
now, so the number can only go up. Raise a floor when you cover something; the day both
read 100.00, delete this and the `--cov-fail-under=0` override with it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Measured 2026-07-23 on the full unit suite. These are floors, not targets: the target is
#: the 100.00 the repository's own coverage config asks for.
FLOORS = {
    "h/services/normalization.py": 89.34,
    "h/services/pdf_math.py": 94.25,
}

ROW = re.compile(r"^(?P<path>\S+\.py)\s+.*?(?P<percent>\d+\.\d+)%")


def measured(report: str) -> dict[str, float]:
    found = {}
    for line in report.splitlines():
        match = ROW.match(line.strip())
        if match and match["path"] in FLOORS:
            found[match["path"]] = float(match["percent"])
    return found


def main(argv: list[str]) -> int:
    report = Path(argv[1]).read_text(encoding="utf-8")
    found = measured(report)

    missing = sorted(set(FLOORS) - set(found))
    if missing:
        sys.stdout.write(f"no coverage reported for: {', '.join(missing)}" + "\n")
        return 1

    failed = False
    for path, floor in sorted(FLOORS.items()):
        percent = found[path]
        status = "ok" if percent >= floor else "BELOW FLOOR"
        sys.stdout.write(f"{path}\t{percent:.2f}%\tfloor {floor:.2f}%\t{status}" + "\n")
        if percent < floor:
            failed = True

    if failed:
        sys.stdout.write(
            "\nCoverage of the recovery fell. Cover what you added, or say in the commit "
            "why the floor should move down.\n"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
