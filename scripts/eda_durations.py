# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Refresh tests/eda_durations.json, which balances the CI eda shards (pytest-split).

Use CI's own timings: local ones do not transfer to the runners, where a cold C++ build
dominates. CI logs them, so for a finished run:

    gh run view RUN --log | cut -f3- | sed -E 's/^[0-9TZ:.-]+ //' > out/eda.log
    uv run python scripts/eda_durations.py out/eda.log

Or locally, run the eda tests with ``--durations=0 --durations-min=0`` in the tools image,
saving the output, then pass that log here:

    uv run pytest -m eda -n 4 --durations=0 --durations-min=0 > out/eda.log
    uv run python scripts/eda_durations.py out/eda.log

A stale file only makes the shards less even; a test it does not list runs anyway.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

LINE = re.compile(r"^\s*([0-9.]+)s\s+(?:setup|call|teardown)\s+(\S.*?)\s*$")
OUT = Path(__file__).parents[1] / "tests" / "eda_durations.json"


def main(log: Path) -> None:
    totals: dict[str, float] = defaultdict(float)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if m := LINE.match(line):
            totals[m[2]] += float(m[1])
    if not totals:
        raise SystemExit(f"{log}: no durations (run pytest with --durations=0)")
    rows = {k: round(totals[k], 2) for k in sorted(totals)}
    OUT.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT.name}: {len(rows)} tests, {sum(rows.values()):.0f} s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
