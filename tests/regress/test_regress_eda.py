# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""bag regress end to end on two small seeds (PR 5): the real lint, compile, generated tests
and formal. The nightly runs many more (PR 6); these pin that the path works."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.flows import lint
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.ir import resolve
from hdl_bag.regress.dsl import AXIL_NA, Cfg
from hdl_bag.regress.generate import check, random_config

# What each seed draws, pinned so a generator change that moves them shows here.
SEEDS = {
    0: {"topologies": ["AXI4-Lite crossbar"], "bridges": [], "port_style": "flat"},
    4: {"topologies": ["AHB-Lite single-layer", "APB4 decoder-mux"],
        "bridges": ["bag_ahb2apb"], "port_style": "sv-interface"},
}  # fmt: skip


@pytest.mark.eda
@pytest.mark.parametrize("seed", sorted(SEEDS))
def test_a_seed_runs_the_whole_flow(tmp_path: Path, seed: int) -> None:
    got = CliRunner().invoke(main, ["regress", "--seed", str(seed), "-o", str(tmp_path),
                                    "--formal-budget", "600"])  # fmt: skip
    result = json.loads((tmp_path / str(seed) / "result.json").read_text(encoding="utf-8"))
    assert {k: result["dimensions"][k] for k in SEEDS[seed]} == SEEDS[seed]
    assert result["findings"] == [], "\n".join(result["findings"])
    assert result["status"] in ("pass", "timeout"), got.output
    assert got.exit_code == 0, got.output
    steps = result["steps"]
    assert [s for s in steps if not s.startswith("formal ")] == [
        "draw", "check", "resolve", "generate", "lint", "compile", "sim"]  # fmt: skip
    assert any(s.endswith("_decode:prove") and v == "pass" for s, v in steps.items())


@pytest.mark.eda
def test_a_design_whose_only_width_converter_is_axi4_lite_lints_clean(tmp_path: Path) -> None:
    """Seed 0's finding (CI dispatch 44024807433), cut down: an AXI4-Lite crossbar with one
    Lite port at another width. slang reported a sign conversion in bag_axi_dw.sv:153,
    which no corpus config had shown. The width-converter template lists bag_axi_dw.sv
    beside bag_axil_dw.sv, so here the AXI4 converter is compiled but not instantiated,
    while every corpus config with a converter also instantiates an AXI4 one."""
    c = Cfg("lite_dw", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("x", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 32, 32, "D0").link("m0", "x", 0).decode("m0", "DECERR")
    c.slave("s0", "AXI4-Lite", 64, 32, "D0").link("x", "s0", 0)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    c.option("m0", **AXIL_NA).option("s0", **AXIL_NA)
    _, result = check(c, tmp_path)
    assert [f.message for f in result.errors] == []
    out = generate(resolve(result), tmp_path / "out", tmp_path)
    report = lint(sources(out), out.top, out.root, tools=out.lint_tools, incdirs=include_dirs(out))
    assert [f.format() for f in report.unwaived] == []


@pytest.mark.eda
def test_a_loop_verilator_sees_only_through_whole_structs_is_not_reported(tmp_path: Path) -> None:
    """Seed 202611220019 (#196): Verilator, which takes each struct as one signal, saw a
    combinational loop through bag_axi_dw's request and response structs and a generated
    READY wire (UNOPTFLAT at bag_axi_dw.sv:237). Yosys finds none at bit level. The config is
    this seed's draw; a generator change that moves it shows as the bridge list here."""
    cfg = random_config(202611220019)
    _, result = check(cfg, tmp_path)
    assert [f.message for f in result.errors] == []
    spec = resolve(result)
    assert sorted(b.template for b in spec.bridges) == ["bag_ahb2axi", "bag_axi2apb"]
    out = generate(spec, tmp_path / "out", tmp_path)
    report = lint(sources(out), out.top, out.root, tools=out.lint_tools, incdirs=include_dirs(out))
    assert [f.format() for f in report.unwaived] == []
