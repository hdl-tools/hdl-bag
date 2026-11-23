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

    def __init__(self, results: str | None, exit_code: int | None = None) -> None:
        self.results = results
        self.exit_code = exit_code

    def build(self, *, build_dir: Path, **_kw: Any) -> None:
        build_dir.mkdir(parents=True, exist_ok=True)

    def test(self, *, results_xml: str, **_kw: Any) -> None:
        if self.results is not None:
            Path(results_xml).write_text(self.results, encoding="utf-8", newline="\n")
        if self.exit_code is not None:
            # What cocotb's runner does when the simulator exits non-zero, which is the only
            # way the results can be half-written: simulate() turns it into the exit code it
            # reports, so a test that never raises here cannot exercise that part of the message.
            raise SystemExit(self.exit_code)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    results: str | None,
    exit_code: int | None = None,
) -> None:
    module = tmp_path / "demo_tests.py"
    module.write_text(MODULE, encoding="utf-8", newline="\n")
    import cocotb_tools.runner

    runner = _Runner(results, exit_code)
    monkeypatch.setattr(cocotb_tools.runner, "get_runner", lambda _sim: runner)
    simulate([tmp_path / "x.sv"], "x", module, tmp_path / "build")


def test_truncated_results_are_an_error_naming_the_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A simulator killed part-way leaves the results half-written. ElementTree's ParseError is
    a SyntaxError, so it passed the CLI's `except (SimUnavailable, ValueError, RuntimeError)`
    and reached the user as a traceback."""
    with pytest.raises(
        RuntimeError,
        match=re.escape("demo_tests.py: the simulation's results are unreadable (exit 2)"),
    ):
        _run(monkeypatch, tmp_path, '<testsuites><testsuite><testcase name="test_one"', 2)


def test_results_that_were_never_written_are_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case that was already handled, kept beside the one that was not."""
    with pytest.raises(
        RuntimeError, match=re.escape("demo_tests.py: the simulation wrote no results")
    ):
        _run(monkeypatch, tmp_path, None)


def test_bag_sim_reports_unreadable_results_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other four fixes have a CLI-level test; this one rested on inference from `_sim`'s
    `except (SimUnavailable, ValueError, RuntimeError)`. ParseError is a SyntaxError, so only
    turning it into a RuntimeError puts it inside that clause."""
    from click.testing import CliRunner

    from hdl_bag.cli import main

    out = tmp_path / "out"
    ir_path = Path(__file__).parents[1] / "golden" / "demo_ss.ir.yaml"
    assert CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(out)]).exit_code == 0
    import cocotb_tools.runner

    runner = _Runner('<testsuites><testsuite><testcase name="test_one"', 2)
    monkeypatch.setattr(cocotb_tools.runner, "get_runner", lambda _sim: runner)
    result = CliRunner().invoke(main, ["sim", str(out)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "the simulation's results are unreadable (exit 2)" in result.output
