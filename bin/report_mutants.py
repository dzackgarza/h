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


def survivors(results: str) -> list[str]:
    """Return the ids listed under the survey's "survived" heading."""
    collected: list[str] = []
    in_survivors = False
    for line in results.splitlines():
        heading = line.strip().lower()
        if heading.startswith("survived"):
            in_survivors = True
            continue
        if heading.startswith(OTHER_OUTCOMES):
            in_survivors = False
            continue
        if not in_survivors or not line.strip():
            continue
        if line.lstrip().startswith("----"):
            # The per-file rule between id groups is not an id.
            continue
        collected.extend(part.strip() for part in line.split(",") if part.strip())
    return collected


def summary(alive: list[str]) -> str:
    """Render the survey as the Markdown that becomes the job summary."""
    if not alive:
        return (
            "## Recovery mutation survey\n\n"
            "No surviving mutants: every change the tool made to the recovery broke at "
            "least one test.\n"
        )
    lines = [
        "## Recovery mutation survey\n",
        f"**{len(alive)} surviving mutants** -- each is a change to the recovery that no "
        "test objects to. Inspect one with `mutmut show <id>`.\n",
    ]
    lines.extend(f"- `{identifier}`" for identifier in alive)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    alive = survivors(Path(argv[1]).read_text(encoding="utf-8"))

    if "--fail-on-survivors" not in argv:
        sys.stdout.write(summary(alive))
        return 0

    if alive:
        sys.stdout.write(
            f"{len(alive)} mutants survived; the recovery has changes no test notices.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
