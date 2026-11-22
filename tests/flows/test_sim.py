# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""simulate() with the cocotb runner stubbed: what it does with the results the runner leaves.

Verilator is not needed, so these are not EDA tests. The runner is the only thing replaced;
everything simulate() does with the results file is real.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from hdl_bag.flows.sim import simulate

MODULE = """
import cocotb


@cocotb.test()
async def test_one(dut):
    pass
"""


class _Runner:
    """A cocotb runner that writes RESULTS instead of simulating."""

    def __init__(self, results: str | None) -> None:
        self.results = results

    def build(self, *, build_dir: Path, **_kw: Any) -> None:
        build_dir.mkdir(parents=True, exist_ok=True)

    def test(self, *, results_xml: str, **_kw: Any) -> None:
        if self.results is not None:
            Path(results_xml).write_text(self.results, encoding="utf-8", newline="\n")


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, results: str | None) -> None:
    module = tmp_path / "demo_tests.py"
    module.write_text(MODULE, encoding="utf-8", newline="\n")
    import cocotb_tools.runner

    monkeypatch.setattr(cocotb_tools.runner, "get_runner", lambda _sim: _Runner(results))
    simulate([tmp_path / "x.sv"], "x", module, tmp_path / "build")


def test_truncated_results_are_an_error_naming_the_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A simulator killed part-way leaves the results half-written. ElementTree's ParseError is
    a SyntaxError, so it passed the CLI's `except (SimUnavailable, ValueError, RuntimeError)`
    and reached the user as a traceback."""
    with pytest.raises(
        RuntimeError, match=re.escape("demo_tests.py: the simulation's results are unread")
    ):
        _run(monkeypatch, tmp_path, '<testsuites><testsuite><testcase name="test_one"')


def test_results_that_were_never_written_are_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case that was already handled, kept beside the one that was not."""
    with pytest.raises(
        RuntimeError, match=re.escape("demo_tests.py: the simulation wrote no results")
    ):
        _run(monkeypatch, tmp_path, None)
