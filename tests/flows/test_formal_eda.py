# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The formal flow against SymbiYosys (eda): prove() on tiny properties, the generated
quiet-in-reset jobs on real designs, a reset bug they must catch, and the time limit."""

import re
from pathlib import Path

import pytest

from hdl_bag.flows import Report, prove, run_formal, run_sby
from hdl_bag.flows.formal import Mode
from hdl_bag.formal_gen import props_module
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


@pytest.mark.parametrize(
    ("config", "jobs"),
    [("ahb_fixed", ("reset",)), ("ahb2apb_pt", ("protocol", "reset")),
     ("apb4_min", ("protocol", "reset")), ("apb4_rules", ("protocol", "reset"))],
)  # fmt: skip
def test_the_generated_jobs_hold(tmp_path: Path, config: str, jobs: tuple[str, ...]) -> None:
    """ahb_fixed asserts its reset synchronously (the synchronizer's stages come first);
    ahb2apb_pt has a bridge with state on both sides; apb4_min has two reset groups and an
    alias pin; apb4_rules has a sync-assertion reset on APB ports, with read-only,
    write-only and secure slaves. Every cover is reached, so no assertion holds vacuously;
    the APB ports also pass ZipCPU's protocol checkers (D60)."""
    spec = load(P2 / f"{config}.ir.yaml")
    out = generate(spec, tmp_path, P2)
    report = run_formal(out, timeout=600)
    assert report.ok, report.format()
    assert out.formal == tuple(f"formal/{spec.project.name}_{j}.sby" for j in jobs)


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
            "formal/demo_ss_reset_prove/engine_0/trace.vcd)") in failed  # fmt: skip


def test_an_apb_protocol_break_is_caught(tmp_path: Path) -> None:
    """With slave dual0's PENABLE wired to its PSEL, PENABLE is high in the setup phase:
    ZipCPU's master checker on that port must fail (D60)."""
    out = generate(load(P2 / "apb4_rules.ir.yaml"), tmp_path, P2)
    top = tmp_path / "rtl" / f"{out.top}.sv"
    old = "assign dual0_apb4_penable = u_apb_m_penable[3];"
    text = top.read_text(encoding="utf-8")
    assert text.count(old) == 1
    top.write_text(text.replace(old, "assign dual0_apb4_penable = u_apb_m_psel[3];"),
                   encoding="utf-8", newline="\n")  # fmt: skip
    report = run_sby(tmp_path / "formal" / "apb4_rules_protocol.sby", tmp_path, timeout=600)
    failed = [(f.rule, f.file) for f in report.findings]
    assert ("FORMAL-ASSERT", "formal/vendor/zipcpu/fapb_master.v") in failed, report.format()


def test_a_job_over_its_time_limit_stops(tmp_path: Path) -> None:
    """demo_ss's cover task takes minutes; with a 5 s limit the job stops with a finding.
    (That every process it started stops too is tested with a stand-in sby in
    test_formal.py.)"""
    generate(load(GOLDEN), tmp_path, GOLDEN.parent)
    report = run_sby(tmp_path / "formal" / "demo_ss_reset.sby", tmp_path, timeout=5)
    assert [f.rule for f in report.findings] == ["FORMAL-TIMEOUT"]
    assert report.findings[0].message.startswith("demo_ss_reset: no verdict within 5 s")


_STEP_COUNTER = """\
module bag_test_step_counter (output reg [3:0] n);
  initial n = 4'd0;
  always @($global_clock) n <= (n == 4'd15) ? n : n + 4'd1;
endmodule
"""


def test_environment_registers_advance_every_solver_step(tmp_path: Path) -> None:
    """The environment's step registers (start-in-reset, reset history, covers) must update
    on every solver step, whatever the clocks do. A register on a net merely marked
    (* gclk *) does not under yosys-slang: the solver toggles that net like one more free
    clock (it did, in PR 1). Here bag_init, set at the start, must have fallen by step 1."""
    spec = load(P2 / "apb4_min.ir.yaml")
    out = generate(spec, tmp_path, P2)
    props = f"u_{props_module(spec)}"
    (tmp_path / "formal" / "step_check.sv").write_text(
        "module step_check (input logic clk_a);\n"
        "  logic [3:0] n;\n"
        "  bag_test_step_counter u_n (.n(n));\n"
        f"  always_comb init_falls_at_once: assert (n == 4'd0 || !{props}.bag_init);\n"
        "endmodule\n"
        f"bind {out.top} step_check u_step_check (.clk_a(clk_a));\n",
        encoding="utf-8",
    )
    (tmp_path / "formal" / "step_counter.v").write_text(_STEP_COUNTER, encoding="utf-8")
    reset_job = (tmp_path / "formal" / "apb4_min_reset.sby").read_text(encoding="utf-8")
    job = tmp_path / "formal" / "step_check.sby"
    job.write_text(
        reset_job.replace("[tasks]\nprove\ncover\n", "[tasks]\nbmc\n")
        .replace("prove: mode prove\nprove: depth 6\ncover: mode cover\ncover: depth 40\n",
                 "bmc: mode bmc\nbmc: depth 4\n")
        .replace("read_slang ", "read_verilog -formal step_counter.v\nread_slang ")
        .replace("apb4_min_reset.sv\nprep", "apb4_min_reset.sv step_check.sv\nprep")
        + "step_check.sv\nstep_counter.v\n",
        encoding="utf-8",
    )  # fmt: skip
    report = run_sby(job, tmp_path, timeout=300)
    assert report.ok, report.format()
