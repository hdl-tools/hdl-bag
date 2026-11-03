# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Refresh tests/configs/waived_counts.json: per gate run, how many findings are waived and which.

test_gate_lint_and_compile writes each run's count under out/waived, then compares it with
the snapshot. After a deliberate change, run the eda tests and then this script, and say in
the PR why the count moved.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUT = ROOT / "tests" / "configs" / "waived_counts.json"


def main() -> None:
    rows = {
        row.pop("run"): row
        for row in (
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((ROOT / "out" / "waived").glob("*.json"))
        )
    }
    if not rows:
        raise SystemExit("out/waived is empty: run pytest -m eda first")
    OUT.write_text(
        json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"{OUT.name}: {len(rows)} runs, {sum(r['count'] for r in rows.values())} waived")


if __name__ == "__main__":
    main()
