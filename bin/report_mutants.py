#!/usr/bin/env python3
"""Turn a mutmut survey into something readable, and into a pass or a failure.

A surviving mutant is a change to the recovery that no test objects to. The survey is only
useful if what survived is visible without anyone re-running it, so this writes the list
into the job summary and, separately, decides the job's status from it.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: `mutmut results` groups ids under a heading per outcome. These end the survivor list.
OTHER_OUTCOMES = ("killed", "timeout", "suspicious", "skipped", "untested")


def by_outcome(results: str) -> dict[str, list[str]]:
    """Group the survey's mutant ids under the outcome heading they appear beneath."""
    grouped: dict[str, list[str]] = {}
    current: str | None = None
    for line in results.splitlines():
        heading = line.strip().lower()
        if heading.startswith(("survived", *OTHER_OUTCOMES)):
            current = heading.split()[0].rstrip(":")
            grouped.setdefault(current, [])
            continue
        if current is None or not line.strip():
            continue
        if line.lstrip().startswith("----"):
            # The per-file rule between id groups is not an id.
            continue
        grouped[current].extend(expand(line))
    return grouped


def expand(line: str) -> list[str]:
    """Expand one line of mutmut ids, whose consecutive runs are printed as `12-15`."""
    ids: list[str] = []
    for part in (piece.strip() for piece in line.split(",")):
        if not part:
            continue
        first, sep, last = part.partition("-")
        if sep and first.isdigit() and last.isdigit():
            ids.extend(str(number) for number in range(int(first), int(last) + 1))
        else:
            ids.append(part)
    return ids


def summary(grouped: dict[str, list[str]]) -> str:
    """Render the survey as the Markdown that becomes the job summary."""
    alive = grouped.get("survived", [])
    tested = sum(len(ids) for ids in grouped.values())
    if not tested:
        return (
            "## Recovery mutation survey\n\n"
            "**The survey tested no mutants.** It did not run, so it says nothing about "
            "the recovery. Read the step log rather than this summary.\n"
        )
    if not alive:
        return (
            "## Recovery mutation survey\n\n"
            f"No surviving mutants out of {tested} tested: every change the tool made to "
            "the recovery broke at least one test.\n"
        )
    lines = [
        "## Recovery mutation survey\n",
        f"**{len(alive)} of {tested} mutants survived** -- each is a change to the "
        "recovery that no test objects to. Inspect one with `mutmut show <id>`.\n",
    ]
    lines.extend(f"- `{identifier}`" for identifier in alive)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    grouped = by_outcome(Path(argv[1]).read_text(encoding="utf-8"))

    if "--fail-on-survivors" not in argv:
        sys.stdout.write(summary(grouped))
        return 0

    tested = sum(len(ids) for ids in grouped.values())
    if not tested:
        # A survey that crashed produces an empty result, and an empty result must never
        # read as "nothing survived".
        sys.stdout.write(
            "The survey tested no mutants, so it proves nothing about the recovery.\n"
        )
        return 1

    if not grouped.get("killed"):
        # Every mutant surviving is not a verdict on the tests, it is a broken survey: a
        # runner with no kill power at all, or a timing model that gave up before it
        # compared anything. Read the run log, not this number.
        sys.stdout.write(
            f"The survey killed nothing out of {tested} mutants, so it is not measuring "
            "the tests. Check the runner and the per-mutant outcomes in the run log.\n"
        )
        return 1

    alive = grouped.get("survived", [])
    if alive:
        sys.stdout.write(
            f"{len(alive)} of {tested} mutants survived; the recovery has changes no "
            "test notices.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
