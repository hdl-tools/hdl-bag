# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generated RTL against the EDA tools (eda): lint, compile and a wiring simulation."""

from pathlib import Path

import pytest
from simrun import REPO, run_cocotb

from hdl_bag.flows import compile_design, lint
from hdl_bag.gen import generate, sources
from hdl_bag.ir import load

CONFIG = REPO / "tests" / "configs" / "p2" / "ahb2apb_pt.ir.yaml"

pytestmark = pytest.mark.eda


@pytest.mark.parametrize("style", ["flat", "sv-interface"])
def test_generated_design(style: str) -> None:
    root = REPO / "out" / "gen" / f"ahb2apb_pt_{style}"
    out = generate(load(CONFIG), root, CONFIG.parent, style)
    files = sources(out)
    for report in (lint(files, out.top, root), compile_design(files, out.top, root)):
        assert report.ok, report.format()
    run_cocotb(
        [root / f for f in files], out.top, Path(__file__).parent / "cocotb_wrapper_ahb2apb_pt.py",
        f"wrapper_ahb2apb_pt_{style}",
    )  # fmt: skip


def test_cli_flow(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from hdl_bag.cli import main

    result = CliRunner().invoke(main, ["flow", str(CONFIG), "-o", str(tmp_path / "flow")])
    assert result.exit_code == 0, result.output
    assert "lint (slang, verilator, verible): 0 unwaived" in result.output
    assert "compile (verilator-build): 0 unwaived" in result.output
    lint_again = CliRunner().invoke(main, ["lint", str(tmp_path / "flow")])
    assert lint_again.exit_code == 0, lint_again.output
