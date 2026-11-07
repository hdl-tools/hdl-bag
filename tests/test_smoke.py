# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The per-PR smoke subset (tests/smoke.py) stays meaningful as the tests change."""

import subprocess
import sys
from pathlib import Path

from smoke import SMOKE, matches

REPO = Path(__file__).parents[1]


def _collect(marks: str) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", "-m", marks],
        cwd=REPO, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [line for line in proc.stdout.splitlines() if "::" in line]


def test_every_smoke_pattern_selects_an_eda_test() -> None:
    """A renamed test or config must not drop out of the smoke subset unnoticed."""
    eda = _collect("eda")
    assert [p for p in SMOKE if not any(matches(t, p) for t in eda)] == []
    smoke = _collect("eda and smoke")
    assert 0 < len(smoke) < len(eda)
    # The golden config is in it whole: both port styles and every route-simulation part.
    golden = [t for t in eda if "golden/demo_ss" in t and ("lint" in t or "route" in t)]
    assert golden and set(golden) <= set(smoke)
