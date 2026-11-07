# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The per-PR smoke subset (tests/smoke.py) stays meaningful as the tests change."""

import json
import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

from smoke import SMOKE, matches

REPO = Path(__file__).parents[1]
# Warm compute seconds from tests/eda_durations.json. The cold CI run of #43 took 6:52 on its
# slowest of four shards for about 1180 s; 2000 s keeps a cold run well under 15 minutes.
BUDGET_S = 2000


@cache
def _collect(marks: str) -> tuple[str, ...]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", "-m", marks],
        cwd=REPO, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        pytest.fail(f"collecting -m {marks!r} failed:\n{proc.stdout}\n{proc.stderr}")
    return tuple(line for line in proc.stdout.splitlines() if "::" in line)


def test_every_smoke_pattern_selects_an_eda_test() -> None:
    """A renamed test or config must not drop out of the smoke subset unnoticed."""
    eda = _collect("eda")
    assert [p for p in SMOKE if not any(matches(t, p) for t in eda)] == []
    smoke = _collect("eda and smoke")
    assert 0 < len(smoke) < len(eda)
    # The golden config is in it whole: both port styles and every route-simulation part.
    golden = [t for t in eda if "golden/demo_ss" in t and ("lint" in t or "route" in t)]
    assert golden and set(golden) <= set(smoke)


def test_every_smoke_mutant_has_its_control_in_the_subset() -> None:
    """A mutant only counts as detected if the same suite passes unmutated in the same run."""
    smoke = _collect("eda and smoke")
    routes = {
        m.group(1) for t in smoke if (m := re.search(r"test_route_simulation\[p\d/(\w+)\]", t))
    }
    mutants = [t for t in smoke if "detect_the" in t and "mutation[" in t]
    assert mutants
    configs = {t.split("[")[1].rstrip("]").split("-")[1] for t in mutants}
    assert sorted(configs - routes) == []


def test_the_smoke_subset_fits_its_time_budget() -> None:
    """pytest-split gives a test with no recorded duration the average, so every smoke test
    needs one for the shards to balance and for this total to mean anything."""
    durations: dict[str, float] = json.loads((REPO / "tests/eda_durations.json").read_text())
    smoke = _collect("eda and smoke")
    assert [t for t in smoke if t not in durations] == []
    assert sum(durations[t] for t in smoke) < BUDGET_S
