# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The nightly report (#59, plan PR 7): pass rate over time, configuration-space coverage and
its gaps, and tonight's open failures with seed, IR and repro command."""

import json
from typing import Any

from hdl_bag.regress.report import coverage, nightly_report
from hdl_bag.regress.run import SeedResult, summary
from hdl_bag.regress.space import Dimensions


def _night(statuses: dict[int, str], dims: dict[int, Dimensions] | None = None) -> dict[str, Any]:
    rs = [SeedResult(s, f"rnd{s}", st, None if st == "pass" else "sim",  # type: ignore[arg-type]
                     findings=[] if st == "pass" else [f"cocotb: error: t{s} failed"],
                     dimensions=(dims or {}).get(s, {}), ir=f"{s}/rnd{s}.ir.yaml",
                     repro=[f"bag regress --seed {s}"])
          for s, st in statuses.items()]  # fmt: skip
    data: dict[str, Any] = json.loads(summary(rs)[0])
    return data


def test_the_summary_records_each_seeds_dimensions() -> None:
    """The coverage report reads them back from earlier nights' summaries (D55: the history is
    in the artifacts)."""
    night = _night({1: "pass"}, {1: {"fabrics": 2, "bridges": ["bag_ahb2apb"]}})
    assert night["seeds"][0]["dimensions"] == {"fabrics": 2, "bridges": ["bag_ahb2apb"]}


def test_coverage_counts_each_dimension_value_drawn_and_passed() -> None:
    rows: list[tuple[Dimensions, bool]] = [
        ({"fabrics": 2, "bridges": ["bag_ahb2apb", "bag_axi2apb"], "secure": True}, True),
        ({"fabrics": 2, "bridges": ["bag_axi2apb"], "secure": False}, False),
    ]
    got = coverage(rows)
    assert got["fabrics"] == {"2": (2, 1)}
    assert got["bridges"] == {"bag_ahb2apb": (1, 1), "bag_axi2apb": (2, 1)}
    assert got["secure"] == {"False": (1, 0), "True": (1, 1)}


def test_the_report_has_the_pass_rate_coverage_gaps_and_open_failures() -> None:
    old = _night({1: "pass", 2: "fail"}, {1: {"map": "dense"}, 2: {"map": "sparse"}})
    tonight = [
        SeedResult(3, "rnd3", "pass", dimensions={"map": "dense"}),
        SeedResult(4, "rnd4", "fail", "lint", findings=["verilator: x [UNOPTFLAT]"],
                   dimensions={"map": "sparse"}, ir="4/rnd4.ir.yaml",
                   repro=["bag regress --seed 4 --formal-budget 600"]),
    ]  # fmt: skip
    text = nightly_report([("2026-11-26", old)], "2026-11-27", tonight)
    assert "| 2026-11-26 | 2 | 1 | 1 | 0 | 0 | 0 | 50% |" in text
    assert "| 2026-11-27 | 2 | 1 | 1 | 0 | 0 | 0 | 50% |" in text
    assert "| map | dense | 2 | 2 |" in text
    assert "| map | sparse | 2 | 0 |" in text
    assert "- map: sparse (drawn 2, never passed)" in text
    assert ("| 4 | fail | lint | verilator: x [UNOPTFLAT] | `4/rnd4.ir.yaml` | "
            "`bag regress --seed 4 --formal-budget 600` |") in text  # fmt: skip


def test_a_night_with_no_failure_says_so() -> None:
    text = nightly_report([], "2026-11-27", [SeedResult(1, "rnd1", "pass")])
    assert "No open failures tonight." in text
    assert "No gaps: every value drawn has passed at least once." in text
