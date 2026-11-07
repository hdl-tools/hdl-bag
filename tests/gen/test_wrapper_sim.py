# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generated RTL against the EDA tools (eda): lint, compile and a wiring simulation."""

from pathlib import Path

import pytest
from simrun import REPO, run_cocotb

from hdl_bag.flows import compile_design, lint
from hdl_bag.gen import generate, sources
from hdl_bag.ir import load

CONFIGS = REPO / "tests" / "configs" / "p2"
CONFIG = CONFIGS / "ahb2apb_pt.ir.yaml"

pytestmark = pytest.mark.eda


# ahb2apb_nested is ahb2apb_pt with the fabrics in nested sub-wrappers: same top ports,
# so the same wiring simulation applies.
@pytest.mark.parametrize("config", ["ahb2apb_pt", "ahb2apb_nested"])
@pytest.mark.parametrize("style", ["flat", "sv-interface"])
def test_generated_design(config: str, style: str) -> None:
    root = REPO / "out" / "gen" / f"{config}_{style}"
    out = generate(load(CONFIGS / f"{config}.ir.yaml"), root, CONFIGS, style)
    files = sources(out)
    for report in (lint(files, out.top, root), compile_design(files, out.top, root, build=False)):
        assert report.ok, report.format()
    run_cocotb(
        [root / f for f in files], out.top, Path(__file__).parent / "cocotb_wrapper_ahb2apb_pt.py",
        f"wrapper_{config}_{style}",
    )  # fmt: skip


def test_cli_flow(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from hdl_bag.cli import main

    result = CliRunner().invoke(main, ["flow", str(CONFIG), "-o", str(tmp_path / "flow")])
    assert result.exit_code == 0, result.output
    assert "lint (slang, verilator, verible): 0 unwaived" in result.output
    assert "compile (verilator-elaborate): 0 unwaived" in result.output
    assert "sim (verilator, cocotb): 0 unwaived" in result.output
    assert "formal (sby): 0 unwaived" in result.output
    lint_again = CliRunner().invoke(main, ["lint", str(tmp_path / "flow")])
    assert lint_again.exit_code == 0, lint_again.output


# Hierarchy variants from the PR 4 review (tests/gen/hier_variants.py): lint and compile.
@pytest.mark.parametrize("variant", ["deep", "sibling2dom", "midlca", "inputpass", "ties_internal"])
@pytest.mark.parametrize("style", ["flat", "sv-interface"])
def test_hierarchy_variant_lints_and_compiles(variant: str, style: str) -> None:
    from hier_variants import build

    root = REPO / "out" / "gen" / f"var_{variant}_{style}"
    out = generate(build(variant), root, CONFIGS, style)
    files = sources(out)
    for report in (lint(files, out.top, root), compile_design(files, out.top, root, build=False)):
        assert report.ok, report.format()
