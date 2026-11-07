# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The formal flow against SymbiYosys (eda): prove() on tiny properties, the generated
quiet-in-reset jobs on real designs, a reset bug they must catch, and the time limit."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hdl_bag.flows import Report, prove, run_formal, run_sby
from hdl_bag.flows.formal import Mode
from hdl_bag.gen import generate
from hdl_bag.ir import load

pytestmark = pytest.mark.eda

P2 = Path(__file__).parents[1] / "configs" / "p2"
GOLDEN = Path(__file__).parents[1] / "golden" / "demo_ss.ir.yaml"

_DESIGN = """\
module t (input logic clk, input logic rst_n, input logic d);
  logic q;
  always_ff @(posedge clk or negedge rst_n) if (!rst_n) q <= 1'b0; else q <= d;
endmodule
"""
_PROPS = """\
module t_props (input logic clk, input logic rst_n, input logic q);
  logic first = 1'b1;
  always_ff @(posedge clk) first <= 1'b0;
  always_comb if (first) assume (!rst_n);
  always_comb if (!rst_n) q_low: assert ({check});
  always_comb q_high: cover ({cover});
endmodule
bind t t_props u_props (.*);
"""


def _tiny(root: Path, check: str, cover: str = "q") -> None:
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "t.sv").write_text(_DESIGN, encoding="utf-8")
    (root / "t_props.sv").write_text(_PROPS.format(check=check, cover=cover), encoding="utf-8")


def _prove(root: Path, mode: Mode = "bmc") -> Report:
    return prove([Path("rtl/t.sv")], "t", root, properties=[Path("t_props.sv")], depth=8,
                 mode=mode)  # fmt: skip


def test_prove_passes_a_property_that_holds(tmp_path: Path) -> None:
    _tiny(tmp_path, "!q")
    modes: tuple[Mode, ...] = ("bmc", "prove", "cover")
    for mode in modes:
        report = _prove(tmp_path, mode)
        assert report.ok, report.format()


def test_prove_reports_a_failing_assertion_where_it_is(tmp_path: Path) -> None:
    _tiny(tmp_path, "q")
    report = _prove(tmp_path)
    assert [(f.rule, f.file, f.line) for f in report.findings] == [
        ("FORMAL-ASSERT", "t_props.sv", 5)
    ]
    assert "assertion q_low fails (counterexample formal/t_bmc/engine_0/trace.vcd)" in (
        report.findings[0].message
    )
    assert (tmp_path / "formal" / "t_bmc" / "engine_0" / "trace.vcd").is_file()


def test_prove_reports_an_unreachable_cover(tmp_path: Path) -> None:
    _tiny(tmp_path, "!q", cover="q && !rst_n")
    report = _prove(tmp_path, "cover")
    assert [(f.rule, f.line) for f in report.findings] == [("FORMAL-COVER", 6)]


@pytest.mark.parametrize("config", ["ahb_fixed", "ahb2apb_pt", "apb4_min"])
def test_the_generated_reset_properties_hold(tmp_path: Path, config: str) -> None:
    """ahb_fixed asserts its reset synchronously (the synchronizer's stages come first);
    ahb2apb_pt has a bridge with state on both sides; apb4_min has two reset groups. Every
    cover is reached, so no assertion holds vacuously."""
    spec = load(P2 / f"{config}.ir.yaml")
    out = generate(spec, tmp_path, P2)
    report = run_formal(out, timeout=600)
    assert report.ok, report.format()
    assert out.formal == (f"formal/{spec.project.name}_reset.sby",)


def test_a_bridge_with_no_reset_is_caught(tmp_path: Path) -> None:
    """With neither side of bag_ahb2apb reset, its APB state machine and request start
    anywhere, so PSEL can reach a slave while the domain is held in reset. (One side alone
    is masked: the other side's reset state selects no slave, as Phase 4 found.)"""
    out = generate(load(P2 / "ahb2apb_pt.ir.yaml"), tmp_path, P2)
    top = tmp_path / "rtl" / f"{out.top}.sv"
    text, n = re.subn(r"(\.[hp]rst_ni\s*)\(bag_rst_D_AON_n\)", r"\1(1'b1)", top.read_text())
    assert n == 2
    top.write_text(text, encoding="utf-8", newline="\n")
    report = run_formal(out, timeout=600)
    failed = [(f.rule, f.message.split(": ", 1)[1]) for f in report.findings]
    assert ("FORMAL-ASSERT", "assertion quiet_wdt0_apb_psel fails (counterexample "
            "formal/demo_ss_reset_bmc/engine_0/trace.vcd)") in failed  # fmt: skip


def test_a_job_over_its_time_limit_stops_with_everything_it_started(tmp_path: Path) -> None:
    """demo_ss's job takes minutes; with a 5 s limit it stops, and no Yosys or solver process
    it started is left running."""
    out = generate(load(GOLDEN), tmp_path, GOLDEN.parent)
    report = run_sby(tmp_path / out.formal[0], tmp_path, timeout=5)
    assert [(f.rule, f.message) for f in report.findings] == [
        ("FORMAL-TIMEOUT", "demo_ss_reset: no verdict within 5 s")
    ]
    if shutil.which("pgrep"):
        left = subprocess.run(["pgrep", "-f", str(tmp_path)], capture_output=True, text=True)
        assert left.stdout == "", left.stdout
