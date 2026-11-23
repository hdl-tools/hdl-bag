# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Print one version's section of CHANGELOG.md, for the release workflow's GitHub release page.

Usage: uv run --no-project python scripts/release_notes.py <version> [changelog]

A section starts at the heading `## [<version>]` and ends before the next `## [` heading or at
the end of the file. The release workflow runs this before anything is published, so a tag
without a changelog section fails while PyPI can still be spared.
"""

import sys
from pathlib import Path

DEFAULT = Path(__file__).parents[1] / "CHANGELOG.md"


def section(text: str, version: str) -> str | None:
    """The lines of `version`'s section, or None when the changelog has no such heading."""
    # The closing bracket in the prefix keeps 1.0.0 from matching 1.0.0rc1 or 1.0.0.1.
    start = f"## [{version}]"
    out: list[str] = []
    found = False
    for line in text.splitlines(keepends=True):
        if line.startswith("## ["):
            if found:
                break
            found = line.startswith(start)
        if found:
            out.append(line)
    return "".join(out).rstrip("\n") + "\n" if out else None


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print("usage: release_notes.py <version> [changelog]", file=sys.stderr)
        return 2
    version, path = argv[0], Path(argv[1]) if len(argv) == 2 else DEFAULT
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"{path.name}: cannot read {path} ({exc.strerror}). Add it before tagging.",
            file=sys.stderr,
        )
        return 1
    notes = section(text, version)
    if notes is None:
        print(
            f"{path.name}: no section '## [{version}]'. Add one for {version} before tagging.",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
