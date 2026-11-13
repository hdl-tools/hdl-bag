# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The formal flow against SymbiYosys (eda): prove() on tiny properties, the generated
quiet-in-reset jobs on real designs, a reset bug they must catch, and the time limit."""

import dataclasses
import re
from pathlib import Path
from typing import Literal

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


# Configs whose every job runs here, as one test; the formal corpus (D76) leaves them out.
GENERATED_JOBS = [
    ("ahb_fixed", ("decode", "protocol", "reset", "route_ahb")),
    ("ahb2apb_pt", ("decode", "protocol", "reset", "route_ahb_bus", "route_aon_apb",
                    "route_br_ahb2aon")),
    ("apb4_min", ("decode", "protocol", "reset", "route_apb")),
    ("apb4_rules", ("decode", "protocol", "reset", "route_apb")),
    ("axil_shared_one", ("decode", "protocol", "reset", "route_bus")),
    ("axi_shared_one", ("decode", "protocol", "reset", "route_bus")),
    ("ahb_single", ("decode", "protocol", "reset", "route_ahb")),
]  # fmt: skip
# Jobs of those configs the formal corpus runs instead, task by task, placed by measured time
# (D83: axi_shared_one's protocol job, on one clock for 16 edges since 76a2fd8, passed 600 s).
IN_CORPUS = {("axi_shared_one", "protocol")}


@pytest.mark.parametrize(("config", "jobs"), GENERATED_JOBS)
def test_the_generated_jobs_hold(tmp_path: Path, config: str, jobs: tuple[str, ...]) -> None:
    """ahb_fixed asserts its reset synchronously and deasserts it asynchronously, so it has
    no synchronizer and its reset acts at once; ahb2apb_pt has a bridge with state on both
    sides; apb4_min has two reset groups and an alias pin; apb4_rules has a sync-assertion
    reset on APB ports, with read-only, write-only and secure slaves; axil_shared_one has
    AXI4-Lite ports; axi_shared_one has AXI4 ports (bag_faxi4; its protocol job runs in the
    formal corpus, IN_CORPUS); ahb_single is the AHB-Lite mutants' base. Every cover is
    reached, so no assertion holds vacuously; the AXI4-Lite and APB ports pass ZipCPU's
    protocol checkers (D60), the AXI4 and AHB-Lite ports BAG's own."""
    where = P3 if config.startswith(("axil", "axi_")) else P2
    spec = load(where / f"{config}.ir.yaml")
    out = generate(spec, tmp_path, where)
    assert out.formal == tuple(f"formal/{spec.project.name}_{j}.sby" for j in jobs)
    here = tuple(f"formal/{spec.project.name}_{j}.sby" for j in jobs
                 if (config, j) not in IN_CORPUS)  # fmt: skip
    report = run_formal(dataclasses.replace(out, formal=here), timeout=600)
    assert report.ok, report.format()


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
FAHB = FAXI4.with_name("bag_fahb.sv")


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
               props: str | None = None, mode: Literal["bmc", "prove", "cover"] = "bmc",
               depth: int = 12, extra: str = "",
               checker: str | None = None) -> Report:  # fmt: skip
    """A design t on one AXI4 port: it drives the slave's signals (DESIGN_IS_SLAVE) or the
    master's, each from DRIVE or 0, with EXTRA (registers) in its body, and takes the others
    from the environment, which bag_faxi4 (or CHECKER, a changed copy) constrains. Returns
    the report of the MODE run."""
    ports = ", ".join(
        f"{'output' if master != design_is_slave else 'input'} logic [{w - 1}:0] {n}"
        for n, w, master in _AXI4
    )
    zero = "'0"
    body = "".join(f"  assign {n} = {drive.get(n, zero)};\n"
                   for n, _, master in _AXI4 if master != design_is_slave)  # fmt: skip
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "t.sv").write_text(
        f"module t (input logic clk, input logic rst_n, {ports});\n{extra}{body}endmodule\n",
        encoding="utf-8",
    )
    text = checker if checker is not None else FAXI4.read_text(encoding="utf-8")
    (root / "bag_faxi4.sv").write_text(text, encoding="utf-8")
    text = props if props is not None else _axi4_props(design_is_slave, check)
    (root / "t_props.sv").write_text(text, encoding="utf-8")
    return prove([Path("rtl/t.sv")], "t", root, properties=[Path("bag_faxi4.sv"),
                 Path("t_props.sv")], depth=depth, mode=mode)  # fmt: skip


def test_an_axi4_slave_environment_answers_no_id_it_was_not_asked(tmp_path: Path) -> None:
    """The design is a master that never asks anything. The environment slave, constrained
    by bag_faxi4, must then never raise RVALID or BVALID, whatever the ID (D64: the
    axi_xbar_rules counterexample came from a slave answering an untracked ID)."""
    report = _axi4_tiny(tmp_path, False, {"bready": "1'b1", "rready": "1'b1"},
                        "    never_answered: assert (!rvalid && !bvalid);")  # fmt: skip
    assert report.ok, report.format()


def test_an_axi4_slave_environment_holds_its_rules_at_every_step(tmp_path: Path) -> None:
    """The same environment, checked at every solver step rather than at clock edges. A
    design can pass what the environment drives straight through to another port, whose
    checker (ZipCPU's AXI4-Lite files) asserts at every step. If bag_faxi4 applied its
    assumptions only at the next edge, the last step of a trace would be left unconstrained:
    axi_adapters' bmc failed that way, with a slave's unasked EXOKAY reaching an AXI4-Lite
    port through the crossbar."""
    props = _axi4_props(False, "").replace(
        "  always_ff @(posedge clk) if (rst_n && was_out) begin\n\n  end\n",
        "  always_comb if (rst_n && was_out) never_answered_now: assert (!rvalid && !bvalid);\n",
    )
    assert "never_answered_now" in props
    report = _axi4_tiny(tmp_path, False, {"bready": "1'b1", "rready": "1'b1"}, props=props)
    assert report.ok, report.format()


# What a design master keeps opening, taking no response, and the table it fills: completed
# writes (B), addresses waiting for data, data bursts waiting for an address, reads (R).
_FILLS = {
    "writes": ({"awvalid": "rst_n", "wvalid": "rst_n", "wlast": "1'b1", "awburst": "2'd1"},
               "tracker_has_room_for_writes"),
    "addresses": ({"awvalid": "rst_n", "awburst": "2'd1"}, "tracker_has_room_for_writes"),
    "data": ({"wvalid": "rst_n", "wlast": "1'b1"}, "tracker_has_room_for_writes"),
    "reads": ({"arvalid": "rst_n", "arburst": "2'd1"}, "tracker_has_room_for_reads"),
}  # fmt: skip


@pytest.mark.parametrize("fill", sorted(_FILLS))
def test_an_axi4_slave_environment_accepts_no_more_than_the_tables_hold(
    tmp_path: Path, fill: str
) -> None:
    """The design is a master that keeps opening single-beat transactions of one kind and
    never takes a response. A crossbar may legally keep more open on one port than
    bag_faxi4's tables hold (axi_xbar_pow2: a 5th read to s3), so the environment slave
    stops accepting once the table they fill is full, as a slave with that acceptance
    capability may (D66). The design's side of the bound, tracker_has_room_*, then holds;
    without the cap it fails within the depth, so the pass is not vacuous (formal review of
    PR 2b)."""
    drive, room = _FILLS[fill]
    report = _axi4_tiny(tmp_path / "cap", False, drive, depth=16)
    assert report.ok, report.format()
    text = FAXI4.read_text(encoding="utf-8")
    assert text.count("        assume (ok_slave_cap);\n") == 1
    uncapped = text.replace("        assume (ok_slave_cap);\n", "")
    report = _axi4_tiny(tmp_path / "uncapped", False, drive, depth=16, checker=uncapped)
    failed = [f.message for f in report.findings if f.rule == "FORMAL-ASSERT"]
    assert any(f"assertion {room} fails" in m for m in failed), report.format()


def test_an_axi4_slave_environment_gives_exokay_only_to_exclusive_accesses(
    tmp_path: Path,
) -> None:
    """The design is a master that keeps making normal (AxLOCK low) single-beat reads and
    writes. EXOKAY answers only an exclusive access (A7.2), so the environment slave must
    never give it here: axi_adapters' bmc failed when a slave's EXOKAY to a normal access
    passed through the fabric to an AXI4-Lite port, where it is illegal."""
    props = _axi4_props(False, "").replace(
        "  always_ff @(posedge clk) if (rst_n && was_out) begin\n\n  end\n",
        "  always_comb if (rst_n && was_out) begin\n"
        "    no_exokay_b: assert (!(bvalid && bresp == 2'b01));\n"
        "    no_exokay_r: assert (!(rvalid && rresp == 2'b01));\n"
        "  end\n",
    )
    assert "no_exokay_r" in props
    report = _axi4_tiny(tmp_path, False, {
        "awvalid": "rst_n", "wvalid": "rst_n", "wlast": "1'b1", "awburst": "2'd1",
        "arvalid": "rst_n", "arburst": "2'd1", "bready": "1'b1", "rready": "1'b1",
    }, props=props)  # fmt: skip
    assert report.ok, report.format()


def test_an_axi4_slave_environment_may_give_exokay_to_an_exclusive_access(
    tmp_path: Path,
) -> None:
    """The control for the test above: with AxLOCK high on single-beat 4-byte accesses at
    address 0 (legal exclusive accesses), the environment slave can still answer EXOKAY on
    both channels, so the EXOKAY rule does not forbid it outright."""
    props = _axi4_props(False, "").replace(
        "  always_ff @(posedge clk) if (rst_n && was_out) begin\n\n  end\n",
        "  always_ff @(posedge clk) if (rst_n && was_out) begin\n"
        "    exokay_b: cover (bvalid && bresp == 2'b01);\n"
        "    exokay_r: cover (rvalid && rresp == 2'b01);\n"
        "  end\n",
    )
    assert "exokay_r" in props
    report = _axi4_tiny(tmp_path, False, {
        "awvalid": "rst_n", "awsize": "3'd2", "awburst": "2'd1", "awlock": "1'b1",
        "wvalid": "rst_n", "wlast": "1'b1", "wstrb": "4'hf",
        "arvalid": "rst_n", "arsize": "3'd2", "arburst": "2'd1", "arlock": "1'b1",
        "bready": "1'b1", "rready": "1'b1",
    }, props=props, mode="cover")  # fmt: skip
    assert report.ok, report.format()


def test_an_address_stored_as_the_write_fifo_fills_and_drains_is_kept(tmp_path: Path) -> None:
    """The design is a slave that always takes addresses and data. The environment master
    sends four single-beat addresses with no data, so bag_faxi4's write FIFO is full (4
    entries, D66), then a fifth address, AWLEN 1, at the same edge as the first burst's WLAST:
    one entry leaves as one arrives. The fifth must be kept, so its burst may then take two
    beats. The FIFO's tail index was computed at 32 bits from a truncated count, so at a full
    count it was out of range (RTL and formal reviews of PR 2b). The LRM drops such a write,
    leaving a copy of the fourth address (AWLEN 0), which forces WLAST on the first beat; yosys
    happened to wrap the index onto the right entry. Checked against a copy of the old code
    with the write dropped at a full count: the cover is then unreached."""
    props = _axi4_props(True, "").replace(
        "  always_ff @(posedge clk) if (rst_n && was_out) begin\n\n  end\n",
        "  logic [2:0] aws = '0;  // addresses accepted\n"
        "  always_ff @(posedge clk)\n"
        "    if (!rst_n) aws <= '0;\n"
        "    else if (awvalid && awready && aws != 3'd7) aws <= aws + 3'd1;\n"
        "  always_comb if (rst_n) begin\n"
        "    if (awvalid) assume (awlen == (aws == 3'd4 ? 8'd1 : 8'd0));\n"
        "    if (aws < 3'd4) assume (!wvalid);\n"
        "    if (aws == 3'd4) assume (wvalid == awvalid && (!awvalid || wlast));\n"
        "    if (aws > 3'd4) assume (!awvalid);\n"
        "  end\n"
        "  always_ff @(posedge clk) if (rst_n && was_out)\n"
        "    two_beat_burst: cover (wvalid && wready && !wlast);\n",
    )
    assert "two_beat_burst" in props
    report = _axi4_tiny(tmp_path, True, {"awready": "1'b1", "wready": "1'b1"}, props=props,
                        mode="cover", depth=24)  # fmt: skip
    assert report.ok, report.format()


@pytest.mark.parametrize("ident", range(4))
@pytest.mark.parametrize(("channel", "rule"), [("b", "b_only_for_a_complete_write"),
                                               ("r", "r_only_for_an_open_read")])  # fmt: skip
def test_an_axi4_slave_design_answering_an_unasked_id_is_caught(
    tmp_path: Path, channel: str, rule: str, ident: int
) -> None:
    """The design is a slave that raises BVALID (or RVALID) on one ID with nothing ever
    asked: the checker must fail it, whichever ID that is (D64)."""
    drive = {f"{channel}valid": "rst_n", f"{channel}id": f"2'd{ident}", "rlast": "1'b1"}
    report = _axi4_tiny(tmp_path, True, drive)
    failed = [f.message for f in report.findings if f.rule == "FORMAL-ASSERT"]
    assert any(f"assertion {rule} fails" in m for m in failed), report.format()


# A design slave answering one single-beat access at a time on each channel, with RESP and
# RLAST as given: it takes an address (with its data, for a write) only while idle, and
# holds its response until taken.
_ANSWERS = """  logic bv = 1'b0, rv = 1'b0;
  logic [1:0] bi = '0, ri = '0;
  always_ff @(posedge clk)
    if (!rst_n) begin bv <= 1'b0; rv <= 1'b0; end
    else begin
      if (bv && bready) bv <= 1'b0;
      else if (!bv && awvalid && wvalid) begin bv <= 1'b1; bi <= awid; end
      if (rv && rready) rv <= 1'b0;
      else if (!rv && arvalid) begin rv <= 1'b1; ri <= arid; end
    end
"""


def _assuming(condition: str) -> str:
    """bag_faxi4 around a design slave, with CONDITION assumed of the environment master
    whenever out of reset: from the first edge, as the checker records from there."""
    props = _axi4_props(True, "").replace(
        "  always_ff @(posedge clk) if (rst_n && was_out) begin\n\n  end\n",
        f"  always_comb if (rst_n) single_beat: assume ({condition});\n",
    )
    assert "single_beat" in props
    return props


def _answering_slave(resp: str, rlast: str) -> dict[str, str]:
    return {"awready": "!bv && awvalid && wvalid", "wready": "!bv && awvalid && wvalid",
            "bvalid": "bv", "bid": "bi", "bresp": resp, "arready": "!rv", "rvalid": "rv",
            "rid": "ri", "rresp": resp, "rlast": rlast}  # fmt: skip


@pytest.mark.parametrize("lock", [0, 1])
def test_an_axi4_slave_design_giving_exokay_to_a_normal_access_is_caught(
    tmp_path: Path, lock: int
) -> None:
    """The design is a slave that answers every single-beat access EXOKAY. For a normal
    access (AxLOCK low) the checker must fail it on both channels (A7.2); for an exclusive
    one it is legal, so nothing fails."""
    props = _assuming(f"wlast && awlen == 8'd0 && arlen == 8'd0 && awlock == 1'b{lock}"
                      f" && arlock == 1'b{lock}")  # fmt: skip
    report = _axi4_tiny(tmp_path, True, _answering_slave("2'b01", "1'b1"), props=props,
                        extra=_ANSWERS)  # fmt: skip
    if lock:
        assert report.ok, report.format()
        return
    failed = {f.message.split(": ", 1)[1].split(" (")[0] for f in report.findings}
    assert failed <= {"assertion b_exokay_only_if_exclusive fails",
                      "assertion r_exokay_only_if_exclusive fails"}, report.format()  # fmt: skip
    assert failed, report.format()


def test_an_axi4_slave_design_ending_a_read_early_or_late_is_caught(tmp_path: Path) -> None:
    """The design is a slave that answers single-beat reads with RLAST low: the checker must
    fail rlast_on_the_last_beat."""
    report = _axi4_tiny(tmp_path, True, _answering_slave("2'b00", "1'b0"),
                        props=_assuming("wlast && awlen == 8'd0 && arlen == 8'd0"),
                        extra=_ANSWERS)  # fmt: skip
    failed = [f.message for f in report.findings if f.rule == "FORMAL-ASSERT"]
    assert failed, report.format()
    assert all("assertion rlast_on_the_last_beat fails" in m for m in failed), report.format()


# (AWADDR, AWLEN, AWSIZE, AWBURST, AWLOCK, legal) on a 32-bit bus, for a design master
# (A3.4.1, A7.2.4).
_BURSTS = [
    ("32'hff0", "8'd3", "3'd2", "2'd1", "1'b0", True),    # INCR ending at the 4 KB line
    ("32'hff4", "8'd3", "3'd2", "2'd1", "1'b0", False),   # INCR crossing it by one beat
    ("32'h44", "8'd15", "3'd2", "2'd2", "1'b0", True),    # WRAP16
    ("32'h0", "8'd2", "3'd2", "2'd2", "1'b0", False),     # WRAP of 3 beats
    ("32'h2", "8'd3", "3'd2", "2'd2", "1'b0", False),     # WRAP not aligned to its size
    ("32'h0", "8'd15", "3'd2", "2'd0", "1'b0", True),     # FIXED of 16 beats
    ("32'h0", "8'd16", "3'd2", "2'd0", "1'b0", False),    # FIXED of 17 beats
    ("32'h0", "8'd0", "3'd3", "2'd1", "1'b0", False),     # AWSIZE above the bus
    ("32'h0", "8'd0", "3'd2", "2'd3", "1'b0", False),     # reserved AWBURST
    ("32'h40", "8'd15", "3'd2", "2'd1", "1'b1", True),    # exclusive, 64 bytes, aligned
    ("32'h0", "8'd2", "3'd2", "2'd1", "1'b1", False),     # exclusive of 12 bytes
    ("32'h4", "8'd1", "3'd2", "2'd1", "1'b1", False),     # exclusive not aligned to 8 bytes
    ("32'h0", "8'd16", "3'd0", "2'd1", "1'b1", False),    # exclusive of 17 beats
]  # fmt: skip


@pytest.mark.parametrize(("addr", "length", "size", "burst", "lock", "legal"), _BURSTS)
def test_a_design_master_burst_is_judged_by_the_axi_rules(
    tmp_path: Path, addr: str, length: str, size: str, burst: str, lock: str, legal: bool
) -> None:
    """The design is a master offering one write address, held until taken: the checker
    passes it if the burst is legal and fails aw_burst_legal if not."""
    report = _axi4_tiny(tmp_path, False, {"awvalid": "rst_n", "awaddr": addr, "awlen": length,
                        "awsize": size, "awburst": burst, "awlock": lock})  # fmt: skip
    if legal:
        assert report.ok, report.format()
        return
    failed = [f.message for f in report.findings if f.rule == "FORMAL-ASSERT"]
    assert failed, report.format()
    assert all("assertion aw_burst_legal fails" in m for m in failed), report.format()


def test_a_design_master_dropping_a_waiting_address_is_caught(tmp_path: Path) -> None:
    """The design is a master whose AWVALID toggles every cycle, whether or not it was
    taken: the checker must fail aw_hold (A3.2.1)."""
    report = _axi4_tiny(
        tmp_path,
        False,
        {"awvalid": "rst_n && tg", "awburst": "2'd1"},
        extra="  logic tg = 1'b0;\n  always_ff @(posedge clk) tg <= !tg;\n",
    )
    failed = [f.message for f in report.findings if f.rule == "FORMAL-ASSERT"]
    assert failed, report.format()
    assert all("assertion aw_hold fails" in m for m in failed), report.format()


def test_a_design_master_ending_a_write_burst_early_is_caught(tmp_path: Path) -> None:
    """The design is a master offering a two-beat write burst whose every beat has WLAST:
    the checker must fail it, by the burst's address if that came first and by the data if
    the data did."""
    report = _axi4_tiny(tmp_path, False, {"awvalid": "rst_n", "awlen": "8'd1",
                        "awburst": "2'd1", "wvalid": "rst_n", "wlast": "1'b1"})  # fmt: skip
    failed = {f.message.split(": ", 1)[1].split(" (")[0] for f in report.findings}
    assert failed, report.format()
    assert failed <= {"assertion wlast_on_the_last_beat fails",
                      "assertion wlast_matches_its_address fails"}, report.format()  # fmt: skip


def test_a_design_master_sending_more_early_data_than_its_address_takes_is_caught(
    tmp_path: Path,
) -> None:
    """CI (mixed_narrow_apb's br_lite route job): write data may come before its address, and
    bag_faxi4 checked an early burst against its address only once the burst had ended. A
    burst still under way when its address came was not checked: the environment master sent
    one beat without WLAST, then the address with AWLEN 0, and the bridge's B for that write
    then failed b_only_for_a_complete_write. Here a design master does the same: one beat
    without WLAST, then the address of a one-beat burst."""
    extra = ("  logic sent = 1'b0;  // the early beat was taken\n"
             "  always_ff @(posedge clk) if (!rst_n) sent <= 1'b0;"
             " else if (wvalid && wready) sent <= 1'b1;\n")  # fmt: skip
    report = _axi4_tiny(tmp_path, False, {"wvalid": "rst_n && !sent", "wlast": "1'b0",
                        "awvalid": "rst_n && sent", "awlen": "8'd0", "awburst": "2'd1"},
                        extra=extra)  # fmt: skip
    failed = {f.message.split(": ", 1)[1].split(" (")[0] for f in report.findings}
    assert failed == {"assertion address_takes_its_early_data fails"}, report.format()


def test_an_ahb_master_environment_holds_its_rules_at_every_step(tmp_path: Path) -> None:
    """As for bag_faxi4: the environment master of a design that is an always-ready AHB-Lite
    slave must keep HSIZE within the bus at every solver step, not only at the edges, or
    whatever the design passes on combinationally is free in a trace's last step."""
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "t.sv").write_text(
        "module t (input logic clk, input logic rst_n, input logic hsel,\n"
        "          input logic [31:0] haddr, input logic [1:0] htrans, input logic hwrite,\n"
        "          input logic [2:0] hsize, input logic [2:0] hburst, input logic [3:0] hprot,\n"
        "          input logic hmastlock, input logic hnonsec, input logic [31:0] hwdata,\n"
        "          output logic hready, output logic hresp, output logic [31:0] hrdata);\n"
        "  assign hready = 1'b1;\n  assign hresp = 1'b0;\n  assign hrdata = '0;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    (tmp_path / "bag_fahb.sv").write_text(FAHB.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "t_props.sv").write_text(
        "module t_props (input logic clk, input logic rst_n, input logic hsel,\n"
        "    input logic [31:0] haddr, input logic [1:0] htrans, input logic hwrite,\n"
        "    input logic [2:0] hsize, input logic [2:0] hburst, input logic [3:0] hprot,\n"
        "    input logic hmastlock, input logic hnonsec, input logic [31:0] hwdata,\n"
        "    input logic hready, input logic hresp, input logic [31:0] hrdata);\n"
        "  logic first = 1'b1, was_out = 1'b0;\n"
        "  always_ff @(posedge clk) begin first <= 1'b0; was_out <= rst_n; end\n"
        "  always_comb if (first) assume (!rst_n);\n"
        "  bag_fahb #(.DesignIsSlave(1)) u_chk (.clk_i(clk), .rst_ni(rst_n), .hsel, .haddr,\n"
        "      .htrans, .hwrite, .hsize, .hburst, .hprot, .hmastlock, .hnonsec, .hwdata,\n"
        "      .hready, .hreadyout(hready), .hresp, .hrdata, .write_done_o(), .read_done_o(),\n"
        "      .wait_o(), .error_o(), .seq_o());\n"
        "  always_comb if (rst_n && was_out && hsel && htrans[1])\n"
        "    size_within_the_bus_now: assert (hsize <= 3'd2);\n"
        "endmodule\n"
        "bind t t_props u_props (.*);\n",
        encoding="utf-8",
    )
    report = prove([Path("rtl/t.sv")], "t", tmp_path, properties=[Path("bag_fahb.sv"),
                   Path("t_props.sv")], depth=8, mode="bmc")  # fmt: skip
    assert report.ok, report.format()


def _ahb_mutant(tmp_path: Path, old: str, new: str) -> list[tuple[str, str]]:
    """ahb_single's protocol job with OLD replaced by NEW in its top; the failed assertions
    as (rule, message up to the counterexample)."""
    out = generate(load(P2 / "ahb_single.ir.yaml"), tmp_path, P2)
    top = tmp_path / "rtl" / f"{out.top}.sv"
    text = top.read_text(encoding="utf-8")
    assert text.count(old) == 1
    top.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    report = run_sby(tmp_path / "formal" / "ahb_single_protocol.sby", tmp_path, timeout=600)
    return [(f.rule, f.message.split(": ", 1)[1].split(" (")[0]) for f in report.findings]


def test_a_one_cycle_ahb_error_is_caught(tmp_path: Path) -> None:
    """With master mcu seeing HRESP only once HREADY is high, an ERROR (a decode miss) shows
    for one cycle instead of two: bag_fahb on that port must fail it (IHI0033A 5.1.3)."""
    failed = _ahb_mutant(
        tmp_path,
        "assign mcu_ahb_hresp = u_ahb_s_hresp[0];",
        "assign mcu_ahb_hresp = u_ahb_s_hresp[0] & u_ahb_s_hready[0];",
    )
    assert any("error_takes_two_cycles fails" in m for _, m in failed), failed


def test_an_ahb_error_dropped_in_its_second_cycle_is_caught(tmp_path: Path) -> None:
    """With master mcu seeing HRESP only while HREADY is low, an ERROR (a decode miss) is
    dropped in its second cycle: bag_fahb on that port must fail error_second_cycle
    (IHI0033A 5.1.3)."""
    failed = _ahb_mutant(
        tmp_path,
        "assign mcu_ahb_hresp = u_ahb_s_hresp[0];",
        "assign mcu_ahb_hresp = u_ahb_s_hresp[0] & ~u_ahb_s_hready[0];",
    )
    assert any("error_second_cycle fails" in m for _, m in failed), failed


def test_a_waiting_ahb_address_dropped_without_an_error_is_caught(tmp_path: Path) -> None:
    """PR 2b review (the slave-port cancel gap): on a slave port bag_fahb let a waiting address
    phase turn into IDLE at any time, since an ERROR from another slave is invisible there.
    While this slave's own data phase holds the bus, the ERROR would be its own, so here the
    fabric drops s12's waiting address phase in its second wait cycle with no ERROR at all."""
    failed = _ahb_mutant(
        tmp_path,
        "assign s12_ahb_htrans = u_ahb_m_htrans[0];",
        "logic bag_mut_q = 1'b0;\n"
        "always_ff @(posedge clk_h) bag_mut_q <= !u_ahb_m_hready[0];\n"
        "assign s12_ahb_htrans = (u_ahb_m_hready[0] || !bag_mut_q) ? u_ahb_m_htrans[0] : 2'b00;",
    )
    assert any("address_holds_while_waiting fails" in m for _, m in failed), failed


def test_an_ahb_slave_whose_hreadyout_is_not_routed_back_is_caught(tmp_path: Path) -> None:
    """With slave s12's HREADY tied high, its own wait states never reach the bus: bag_fahb
    on that port must fail the routing rule."""
    failed = _ahb_mutant(
        tmp_path, "assign s12_ahb_hready = u_ahb_m_hready[0];", "assign s12_ahb_hready = 1'b1;"
    )
    assert any("hreadyout_reaches_hready fails" in m for _, m in failed), failed


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


def _dw_harness(root: Path, up: int, down: int, checks: str) -> list[Path]:
    """bag_axi_dw from UP-bit data (its slave port, driven by an environment master) to
    DOWN-bit data (its master port, answered by an environment slave), with bag_faxi4 on each
    side: the near one assumes the environment's AXI4 rules, the far one asserts the
    converter's; CHECKS are more statements, out of reset. Returns the files to prove, in
    compile order (the PULP files and the wrapper's from mixed_wide_to_narrow, which has
    one)."""
    spec = load(P3 / "mixed_wide_to_narrow.ir.yaml")
    out = generate(spec, root, P3)
    files = [Path(f) for f in (root / f"{spec.project.name}.f").read_text(
        encoding="utf-8").split() if f.startswith("rtl/") and "/pkg/" not in f
        and f != f"rtl/{out.top}.sv"]  # fmt: skip
    side = {"s": (up, True), "m": (down, False)}
    ports: list[str] = []
    conns: dict[str, list[str]] = {"s": [], "m": []}
    for p, (width, design_is_slave) in side.items():
        for n, w, master in _AXI4:
            w = width if n == "wdata" or n == "rdata" else width // 8 if n == "wstrb" else w
            w = 1 if n.endswith("id") else w
            into_dw = master == design_is_slave
            ports.append(f"{'input' if into_dw else 'output'} logic [{w - 1}:0] {p}_{n}")
            conns[p].append(f".{n}({p}_{n})")
    dw_conns = ", ".join(
        f".{p}_{n}_{'i' if (master == ds) else 'o'}({p}_{n})"
        for p, (_, ds) in side.items() for n, _, master in _AXI4
    )  # fmt: skip
    users = (
        ".s_awuser_i('0), .s_wuser_i('0), .s_aruser_i('0), .s_buser_o(), .s_ruser_o(), "
        ".m_awuser_o(), .m_wuser_o(), .m_aruser_o(), .m_buser_i('0), .m_ruser_i('0)"
    )
    (root / "rtl" / "t.sv").write_text(
        f"module t (input logic clk, input logic rst_n, {', '.join(ports)});\n"
        f"  bag_axi_dw #(.AddrWidth(32), .SlvDataWidth({up}), .MstDataWidth({down}),"
        " .SlvIdWidth(1), .MstIdWidth(1), .UserWidth(1), .MaxReads(4)) u_dw (\n"
        f"      .clk_i(clk), .rst_ni(rst_n), {dw_conns}, {users});\nendmodule\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "bag_faxi4.sv").write_text(FAXI4.read_text(encoding="utf-8"), encoding="utf-8")
    checkers = "".join(
        f"  bag_faxi4 #(.DesignIsSlave({int(ds)}), .AddrWidth(32), .DataWidth({width}),"
        f" .IdWidth(1)) u_{p} (.clk_i(clk), .rst_ni(rst_n), {', '.join(conns[p])});\n"
        for p, (width, ds) in side.items()
    )
    (root / "t_props.sv").write_text(
        f"module t_props (input logic clk, input logic rst_n, "
        f"{', '.join(x.replace('output', 'input') for x in ports)});\n"
        "  logic first = 1'b1, was_out = 1'b0;\n"
        "  always_ff @(posedge clk) begin first <= 1'b0; was_out <= rst_n; end\n"
        "  always_comb if (first) assume (!rst_n);\n"
        "  // Out of reset for good: a reset pulse between two clock edges clears the design\n"
        "  // (asynchronously) unseen by the checkers, which sample at edges, and D51's reset\n"
        "  // sequence rules it out. A reset in the middle of a transfer is the reset job's.\n"
        "  always_comb if (was_out) assume (rst_n);\n"
        "  // Quiet until bag_faxi4 checks (its first edge out of reset), as generated jobs are.\n"
        "  always_comb if (!(rst_n && was_out))\n"
        "    assume (!s_awvalid && !s_wvalid && !s_arvalid && !m_bvalid && !m_rvalid);\n"
        f"{checkers}"
        f"  always_comb if (rst_n) begin\n{checks}  end\n"
        "endmodule\n"
        "bind t t_props u_props (.*);\n",
        encoding="utf-8",
        newline="\n",
    )
    return [*files, Path("rtl/t.sv")]


def _dw_prove(root: Path, up: int, down: int, checks: str, depth: int = 10,
              timeout: float = 1200) -> None:  # fmt: skip
    """CHECKS (statements, out of reset) hold on the _dw_harness to DEPTH, and every cover
    among them is reached, each task within TIMEOUT seconds."""
    files = _dw_harness(root, up, down, checks)
    incdirs = [Path("rtl/vendor/pulp/axi/include"), Path("rtl/vendor/pulp/common_cells/include")]
    props = [Path("bag_faxi4.sv"), Path("t_props.sv")]
    modes: tuple[Mode, ...] = ("bmc", "cover")
    for mode in modes:
        report = prove(files, "t", root, properties=props, depth=depth, incdirs=incdirs, mode=mode,
                       timeout=timeout)  # fmt: skip
        assert report.ok, report.format()


@pytest.mark.parametrize(("up", "down"), [(32, 64), (64, 32)])
def test_a_width_converter_keeps_exclusive_accesses_legal(
    tmp_path: Path, up: int, down: int
) -> None:
    """PULP's axi_dw_upsizer widens every modifiable INCR burst and ignores AxLOCK: an
    exclusive write of 2 beats of 4 bytes at 0x...08 left it as 1 beat of 64, not aligned
    to its total (AXI A7.2.4; mixed_wide_to_narrow's protocol bmc, aw_burst_legal on port
    mem). Its downsizer splits beats the same way: 16 beats of 8 bytes became 32 of 4, over
    the 16 an exclusive access may have. bag_axi_dw must pass exclusive accesses on legally,
    both ways; the covers show an exclusive burst of more than one beat gets through."""
    _dw_prove(tmp_path, up, down,
              "    exclusive_write_through: cover (m_awvalid && m_awready && m_awlock\n"
              "                                    && m_awlen != 0);\n"
              "    exclusive_read_through: cover (m_arvalid && m_arready && m_arlock\n"
              "                                   && m_arlen != 0);\n")  # fmt: skip


@pytest.mark.deep
def test_a_downsized_write_response_holds_until_taken(tmp_path: Path) -> None:
    """D88 (owner, 2026-11-14: fix in bag_axi_dw, PULP untouched): PULP's axi_dw_downsizer
    answers a write with a response it merges for the write its W side is on, so a finished
    write's B waiting for BREADY changed when the W side moved on, SLVERR to OKAY
    (mixed_adapters_bridges' protocol bmc on boolector, port dbg, faxil_slave.v:341; upstream
    pulp-platform/axi issue 289). The same holds for the bursts it refuses (WRAP, multi-beat
    FIXED) and answers itself. bag_faxi4's b_hold on the slave side checks it, with
    back-to-back writes of one or two beats; the cover shows an error response held while
    the next write goes on. Unfixed, b_hold failed at step 18 (single-beat writes hid it at
    16), so the bmc runs to 19: about 20 minutes, nightly only (deep)."""
    _dw_prove(tmp_path, 64, 32,
              "    assume (!s_arvalid);\n"
              "    if (s_awvalid) assume (s_awlen <= 8'd1);\n"
              "    error_held_with_a_write_on: cover (s_bvalid && !s_bready && s_bresp[1]\n"
              "                                      && m_awvalid);\n",
              depth=19, timeout=3600)  # fmt: skip


def test_an_exclusive_write_too_long_to_downsize_changes_no_byte(tmp_path: Path) -> None:
    """An exclusive write that would leave the downsizer over 16 beats goes on as a normal
    write with no byte strobe set, so it fails as an exclusive access may (owner,
    2026-11-12): with the environment sending only such writes, no beat reaching the slave
    may write a byte, and one does reach it."""
    _dw_prove(tmp_path, 64, 32,
              "    assume (!s_arvalid);\n"
              "    if (s_awvalid) assume (s_awlock && s_awlen == 8'd15 && s_awsize == 3'd3);\n"
              "    no_byte_written: assert (!m_wvalid || m_wstrb == '0);\n"
              "    not_exclusive: assert (!m_awvalid || !m_awlock);\n"
              "    write_reaches_the_slave: cover (m_wvalid && m_wready);\n")  # fmt: skip
