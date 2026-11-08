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
P3 = Path(__file__).parents[1] / "configs" / "p3"
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
     ("apb4_min", ("protocol", "reset")), ("apb4_rules", ("protocol", "reset")),
     ("axil_shared_one", ("protocol", "reset"))],
)  # fmt: skip
def test_the_generated_jobs_hold(tmp_path: Path, config: str, jobs: tuple[str, ...]) -> None:
    """ahb_fixed asserts its reset synchronously (the synchronizer's stages come first);
    ahb2apb_pt has a bridge with state on both sides; apb4_min has two reset groups and an
    alias pin; apb4_rules has a sync-assertion reset on APB ports, with read-only,
    write-only and secure slaves; axil_shared_one has AXI4-Lite ports. Every cover is
    reached (the protocol job's at its bmc depth), so no assertion holds vacuously; the
    AXI4-Lite and APB ports pass ZipCPU's protocol checkers (D60)."""
    where = P3 if config.startswith("axil") else P2
    spec = load(where / f"{config}.ir.yaml")
    out = generate(spec, tmp_path, where)
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


def _axil_mutant(tmp_path: Path, bvalid: str) -> Report:
    """axil_shared_one's protocol job with master m0's BVALID driven by BVALID instead."""
    out = generate(load(P3 / "axil_shared_one.ir.yaml"), tmp_path, P3)
    top = tmp_path / "rtl" / f"{out.top}.sv"
    old = "assign m0_axil_bvalid = u_bus_s_bvalid[0];"
    text = top.read_text(encoding="utf-8")
    assert text.count(old) == 1
    top.write_text(text.replace(old, f"assign m0_axil_bvalid = {bvalid};"), encoding="utf-8",
                   newline="\n")  # fmt: skip
    return run_sby(tmp_path / "formal" / "axil_shared_one_protocol.sby", tmp_path, timeout=600)


def test_an_axi4_lite_response_with_no_request_is_caught(tmp_path: Path) -> None:
    """With master m0 also seeing BVALID whenever it issues a read, a write response can come
    with no write outstanding: ZipCPU's slave checker on that port must fail (D60)."""
    report = _axil_mutant(tmp_path, "u_bus_s_bvalid[0] | m0_axil_arvalid")
    failed = [(f.rule, f.file) for f in report.findings]
    assert ("FORMAL-ASSERT", "formal/vendor/zipcpu/faxil_slave.v") in failed, report.format()


def test_a_response_that_waits_for_ready_fails_its_cover(tmp_path: Path) -> None:
    """With m0's BVALID shown only while BREADY is high, a write response waits for READY.
    No safety property sees that (D38), but the stall cover on that port can no longer be
    reached: the covers catch what the assertions can't."""
    report = _axil_mutant(tmp_path, "u_bus_s_bvalid[0] & m0_axil_bready")
    assert [(f.rule, f.message.split(": ", 1)[1].split(" not")[0]) for f in report.findings] == [
        ("FORMAL-COVER", "cover proto_b_stall_m0")
    ]


def test_an_ungated_pslverr_in_the_apb_mux_is_caught(tmp_path: Path) -> None:
    """D61: bag_apb_mux drives PSLVERR only in the access phase of a completing transfer. With
    that gating removed (in the generated copy of the mux), a slave's stray PSLVERR reaches
    the master port, and fapb_slave there must fail. That needs the slave environment free to
    raise PSLVERR outside a transfer, as the spec allows: fapb_master on the slave ports sees
    only the spec-defined part of PREADY and PSLVERR (formal review of #46)."""
    generate(load(P2 / "apb4_rules.ir.yaml"), tmp_path, P2)
    mux = tmp_path / "rtl" / "inhouse" / "bag_apb_mux.sv"
    old = "assign s_pslverr_o = s_psel_i & s_penable_i & rsp_ready & rsp_err;"
    text = mux.read_text(encoding="utf-8")
    assert text.count(old) == 1
    # Ungate only what a slave drives: the mux's own error (a miss) stays gated, so only a
    # slave's stray PSLVERR can show the missing gate.
    new = "assign s_pslverr_o = err ? (s_psel_i & s_penable_i & rsp_ready & rsp_err) : rsp_err;"
    mux.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    report = run_sby(tmp_path / "formal" / "apb4_rules_protocol.sby", tmp_path, timeout=600)
    failed = [(f.rule, f.file) for f in report.findings]
    assert ("FORMAL-ASSERT", "formal/vendor/zipcpu/fapb_slave.v") in failed, report.format()


# bag_faxi4 on its own, around a tiny design: every AXI4 signal, its width, and whether the
# master drives it. IDs are 2 bits.
_AXI4 = [("awvalid", 1, True), ("awready", 1, False), ("awid", 2, True), ("awaddr", 32, True),
         ("awlen", 8, True), ("awsize", 3, True), ("awburst", 2, True), ("awlock", 1, True),
         ("awcache", 4, True), ("awprot", 3, True), ("awqos", 4, True), ("awregion", 4, True),
         ("wvalid", 1, True), ("wready", 1, False), ("wdata", 32, True), ("wstrb", 4, True),
         ("wlast", 1, True), ("bvalid", 1, False), ("bready", 1, True), ("bid", 2, False),
         ("bresp", 2, False), ("arvalid", 1, True), ("arready", 1, False), ("arid", 2, True),
         ("araddr", 32, True), ("arlen", 8, True), ("arsize", 3, True), ("arburst", 2, True),
         ("arlock", 1, True), ("arcache", 4, True), ("arprot", 3, True), ("arqos", 4, True),
         ("arregion", 4, True), ("rvalid", 1, False), ("rready", 1, True), ("rid", 2, False),
         ("rdata", 32, False), ("rresp", 2, False), ("rlast", 1, False)]  # fmt: skip
FAXI4 = Path(__file__).parents[2] / "src" / "hdl_bag" / "formal" / "bag_faxi4.sv"


def _axi4_props(design_is_slave: bool, check: str) -> str:
    """bag_faxi4 bound into t, the first cycle in reset, and CHECK (statements sampled at each
    edge after the first out of reset)."""
    ins = ", ".join(f"input logic [{w - 1}:0] {n}" for n, w, _ in _AXI4)
    conns = ", ".join(f".{n}({n})" for n, _, _ in _AXI4)
    return (
        f"module t_props (input logic clk, input logic rst_n, {ins});\n"
        "  logic first = 1'b1, was_out = 1'b0;\n"
        "  always_ff @(posedge clk) begin first <= 1'b0; was_out <= rst_n; end\n"
        "  always_comb if (first) assume (!rst_n);\n"
        f"  bag_faxi4 #(.DesignIsSlave({int(design_is_slave)}), .AddrWidth(32), .DataWidth(32),"
        " .IdWidth(2)) u_chk (\n"
        f"      .clk_i(clk), .rst_ni(rst_n), {conns});\n"
        f"  always_ff @(posedge clk) if (rst_n && was_out) begin\n{check}\n  end\n"
        "endmodule\n"
        "bind t t_props u_props (.*);\n"
    )


def _axi4_tiny(root: Path, design_is_slave: bool, drive: dict[str, str], check: str = "",
               props: str | None = None) -> Report:  # fmt: skip
    """A design t on one AXI4 port: it drives the slave's signals (DESIGN_IS_SLAVE) or the
    master's, each from DRIVE or 0, and takes the others from the environment, which
    bag_faxi4 constrains. Returns the bmc report."""
    ports = ", ".join(
        f"{'output' if master != design_is_slave else 'input'} logic [{w - 1}:0] {n}"
        for n, w, master in _AXI4
    )
    zero = "'0"
    body = "".join(f"  assign {n} = {drive.get(n, zero)};\n"
                   for n, _, master in _AXI4 if master != design_is_slave)  # fmt: skip
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "t.sv").write_text(
        f"module t (input logic clk, input logic rst_n, {ports});\n{body}endmodule\n",
        encoding="utf-8",
    )
    (root / "bag_faxi4.sv").write_text(FAXI4.read_text(encoding="utf-8"), encoding="utf-8")
    text = props if props is not None else _axi4_props(design_is_slave, check)
    (root / "t_props.sv").write_text(text, encoding="utf-8")
    return prove([Path("rtl/t.sv")], "t", root, properties=[Path("bag_faxi4.sv"),
                 Path("t_props.sv")], depth=12, mode="bmc")  # fmt: skip


def test_an_axi4_slave_environment_answers_no_id_it_was_not_asked(tmp_path: Path) -> None:
    """The design is a master that never asks anything. The environment slave, constrained
    by bag_faxi4, must then never raise RVALID or BVALID, whatever the ID (D64: the
    axi_xbar_rules counterexample came from a slave answering an untracked ID)."""
    report = _axi4_tiny(tmp_path, False, {"bready": "1'b1", "rready": "1'b1"},
                        "    never_answered: assert (!rvalid && !bvalid);")  # fmt: skip
    assert report.ok, report.format()


def test_an_axi4_slave_design_answering_an_unasked_id_is_caught(tmp_path: Path) -> None:
    """The design is a slave that raises BVALID on ID 3 with no write ever made: the checker
    must fail it, whichever ID that is (D64)."""
    report = _axi4_tiny(tmp_path, True, {"bvalid": "rst_n", "bid": "2'd3"})
    failed = [f.message for f in report.findings if f.rule == "FORMAL-ASSERT"]
    assert any("assertion b_only_for_a_complete_write fails" in m for m in failed), report.format()


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


def _step_job(tmp_path: Path, register: str) -> tuple[Path, Path]:
    """A bmc job over apb4_min's generated quiet-in-reset module plus a check that REGISTER
    (an expression, 1 at the start) has fallen by step 1, measured against a Verilog
    $global_clock counter. Returns the job and the model it checks."""
    spec = load(P2 / "apb4_min.ir.yaml")
    out = generate(spec, tmp_path, P2)
    formal = tmp_path / "formal"
    (formal / "step_check.sv").write_text(
        "module step_check (input logic clk_a);\n"
        "  logic [3:0] n;\n"
        "  bag_test_step_counter u_n (.n(n));\n"
        "  (* gclk *) logic attr_step;  // the construct PR 1 used, for the control\n"
        "  logic attr_init = 1'b1;\n"
        "  always_ff @(posedge attr_step) attr_init <= 1'b0;\n"
        f"  always_comb init_falls_at_once: assert (n == 4'd0 || !({register}));\n"
        "endmodule\n"
        f"bind {out.top} step_check u_step_check (.clk_a(clk_a));\n",
        encoding="utf-8",
    )
    (formal / "step_counter.v").write_text(_STEP_COUNTER, encoding="utf-8")
    text = (formal / "apb4_min_reset.sby").read_text(encoding="utf-8")
    for old, new in [
        ("[tasks]\nprove\ncover\n", "[tasks]\nbmc\n"),
        ("prove: mode prove\nprove: depth 6\ncover: mode cover\ncover: depth 40\n",
         "bmc: mode bmc\nbmc: depth 4\n"),
        ("read_slang ", "read_verilog -formal step_counter.v\nread_slang "),
        ("apb4_min_reset.sv\nprep", "apb4_min_reset.sv step_check.sv\nprep"),
    ]:  # fmt: skip
        assert text.count(old) == 1, old  # the job text must still have this shape
        text = text.replace(old, new)
    job = formal / "step_check.sby"
    job.write_text(text + "step_check.sv\nstep_counter.v\n", encoding="utf-8")
    return job, formal / "step_check_bmc" / "model" / "design_smt2.smt2"


def test_environment_registers_advance_every_solver_step(tmp_path: Path) -> None:
    """The environment's step registers (start-in-reset, reset history, covers) must update
    on every solver step, whatever the clocks do. Here bag_init, set at the start, must have
    fallen by step 1, and the model checked holds that assertion (a pass with the property
    left out would prove nothing)."""
    spec = load(P2 / "apb4_min.ir.yaml")
    job, model = _step_job(tmp_path, f"u_{props_module(spec)}.bag_init")
    report = run_sby(job, tmp_path, timeout=300)
    assert report.ok, report.format()
    assert "yosys-smt2-assert 1 u_step_check.init_falls_at_once" in model.read_text(
        encoding="utf-8"
    )


def test_a_register_on_a_gclk_marked_net_fails_the_same_check(tmp_path: Path) -> None:
    """The control: the same check on a register clocked by a net marked (* gclk *), the
    construct PR 1 used, fails, because yosys-slang leaves that net an ordinary free one."""
    job, _ = _step_job(tmp_path, "attr_init")
    report = run_sby(job, tmp_path, timeout=300)
    assert [(f.rule, f.message.split(": ", 1)[1].split(" (")[0]) for f in report.findings] == [
        ("FORMAL-ASSERT", "assertion init_falls_at_once fails")
    ]
