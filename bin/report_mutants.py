#!/usr/bin/env python3
"""Read a mutation survey's own stats, and decide the job from them.

`mutmut export-cicd-stats` writes the counts as JSON, so nothing here parses console
output. What matters is that three different outcomes are told apart:

  survivors        changes to the code no test objects to -- the finding, and a failure
  nothing tested   the survey did not run; it says nothing, and must never read as clean
  nothing killed   the survey ran but has no kill power, so it is not measuring the tests

The middle two are the ones that produce a green badge with no proof behind it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def verdict(stats: dict[str, int], survivors: str = "") -> tuple[int, str]:
    """Return the exit status and the Markdown to publish for this survey."""
    total = stats.get("total", 0)
    killed = stats.get("killed", 0)
    survived = stats.get("survived", 0)
    timed_out = stats.get("timeout", 0)
    head = "## Recovery mutation survey\n"

    if not total:
        return 1, (
            f"{head}\n**The survey tested no mutants.** It did not run, so it says "
            "nothing about the recovery. Read the run log rather than this summary.\n"
        )

    if not killed:
        return 1, (
            f"{head}\n**{total} mutants tested and none killed.** That is not a verdict "
            "on the tests, it is a survey that is not measuring them -- a runner with no "
            "kill power, or an environment the tests cannot run in. Read the run log.\n"
        )

    counts = (
        f"{killed} killed, {survived} survived, {timed_out} timed out, "
        f"out of {total} tested."
    )
    if not survived:
        return 0, f"{head}\nEvery mutant died: {counts}\n"

    body = [
        head,
        f"\n**{survived} of {total} mutants survived** -- each is a change to the "
        f"recovery that no test objects to. {counts}\n",
    ]
    if survivors:
        body.append("\n<details><summary>Survivors</summary>\n\n```\n")
        body.append(survivors.strip())
        body.append("\n```\n</details>\n")
    return 1, "".join(body)


def main(argv: list[str]) -> int:
    stats = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    listing = Path(argv[2]) if len(argv) > 2 and Path(argv[2]).exists() else None
    status, report = verdict(
        stats, listing.read_text(encoding="utf-8") if listing else ""
    )

    if "--fail-on-survivors" in argv:
        if status:
            sys.stdout.write(report.splitlines()[2] + "\n")
        return status

    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
