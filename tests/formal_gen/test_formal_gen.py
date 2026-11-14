# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation without EDA tools: what the quiet-in-reset properties watch, their
environment, the SymbiYosys job, and that every port they bind to exists on the top."""

import re
from pathlib import Path

import pytest

import hdl_bag
from hdl_bag.flows.formal import job_tasks
from hdl_bag.formal_gen import (
    COVER_DEPTH,
    PROTOCOL_DEPTH,
    PROVE_DEPTH,
    ahb_ports,
    axi4_ports,
    checkers,
    formal_jobs,
    props_module,
)
from hdl_bag.formal_gen.depth import PATH_EDGES, PATH_EDGES_PER_HOP
from hdl_bag.formal_gen.units import (
    ROUTE_CYCLES,
    ROUTE_CYCLES_PER_FIFO,
    ROUTE_DEPTH,
    ROUTE_DEPTH_LITE_FIFO,
    SCOREBOARD,
    id_prefixes,
)
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.ir import load
from hdl_bag.protocols import Protocol
from hdl_bag.templates import builtin_registry

TESTS = Path(__file__).parents[1]
GOLDEN = TESTS / "golden" / "demo_ss.ir.yaml"
P2, P3 = TESTS / "configs" / "p2", TESTS / "configs" / "p3"
CONFIGS = [GOLDEN, *sorted(P2.glob("*.ir.yaml")), *sorted(P3.glob("*.ir.yaml"))]
_PORT = re.compile(r"^\s*(?:input|output)\s+logic\s*(?:\[[^\]]+\]\s*)?(\w+)", re.M)


def _props(tmp_path: Path, config: Path, style: str | None = None) -> str:
    spec = load(config)
    generate(spec, tmp_path, config.parent, style)
    return (tmp_path / "formal" / f"{spec.project.name}_reset.sv").read_text(encoding="utf-8")


def _opaque(config: Path) -> bool:
    registry = builtin_registry()
    return any((m := registry.get(f.template)) and m.opaque for f in load(config).fabrics)


def test_each_protocol_watches_what_its_port_carries_in_reset(tmp_path: Path) -> None:
    """From demo_ss: an AXI master port (cpu0) gets no response VALID, and its requests are
    assumed quiet; an AXI slave port (ddr0) gets no request VALID; an AHB-Lite master (mcu0)
    sees HREADY high and drives HTRANS IDLE; an AHB-Lite slave (itcm0) gets HTRANS IDLE and
    holds HREADYOUT high; an APB slave (uart0) gets no PSEL."""
    text = _props(tmp_path, GOLDEN)
    cpu, sys = "always_comb if (bag_quiet_D_CPU)", "always_comb if (bag_quiet_D_SYS)"
    aon, per = "always_comb if (bag_quiet_D_AON)", "always_comb if (bag_quiet_D_PER)"

    def env(d: str) -> str:  # in reset, to the edge after it, and until out of reset
        return f"always_comb if (bag_env_quiet_D_{d}) assume"

    for line in [
        f"{cpu} quiet_cpu0_axi_bvalid: assert (cpu0_axi_bvalid == 1'd0);",
        f"{cpu} quiet_cpu0_axi_rvalid: assert (cpu0_axi_rvalid == 1'd0);",
        f"{env('CPU')} (cpu0_axi_awvalid == 1'd0);",
        f"{sys} quiet_ddr0_axi_awvalid: assert (ddr0_axi_awvalid == 1'd0);",
        f"{env('SYS')} (ddr0_axi_bvalid == 1'd0);",
        f"{aon} quiet_mcu0_ahb_hready: assert (mcu0_ahb_hready == 1'd1);",
        f"{env('AON')} (mcu0_ahb_htrans == 2'd0);",
        f"{aon} quiet_itcm0_ahb_htrans: assert (itcm0_ahb_htrans == 2'd0);",
        f"{env('AON')} (itcm0_ahb_hreadyout == 1'd1);",
        f"{per} quiet_uart0_apb_psel: assert (uart0_apb_psel == 1'd0);",
    ]:
        assert f"  {line}\n" in text, line
    assert "quiet_cpu0_axi_awvalid" not in text  # the master drives it: assumed, not asserted


def test_each_cover_needs_the_reset_to_arrive_mid_activity(tmp_path: Path) -> None:
    """A cover per asserted signal: active in the step before its reset (after a reset that
    took effect, in every domain of the group), then the window the assertion checks."""
    text = _props(tmp_path, GOLDEN)
    done = "(bag_done_D_AON && bag_done_D_CPU && bag_done_D_PER && bag_done_D_SYS)"
    for line in [
        f"bag_step_ff0 u_bag_busy_cpu0_axi_bvalid (.d({done} && !bag_held_D_CPU && "
        "cpu0_axi_bvalid != 1'd0), .q(bag_busy_cpu0_axi_bvalid));",
        "bag_step_ff0 u_bag_hit_cpu0_axi_bvalid (.d(bag_hit_cpu0_axi_bvalid || "
        "(bag_held_D_CPU && bag_busy_cpu0_axi_bvalid)), .q(bag_hit_cpu0_axi_bvalid));",
        "always_comb reset_mid_cpu0_axi_bvalid: cover "
        "(bag_quiet_D_CPU && bag_hit_cpu0_axi_bvalid);",
        "bag_step_ff0 u_bag_done_D_CPU (.d(bag_done_D_CPU || bag_quiet_D_CPU), "
        ".q(bag_done_D_CPU));",
    ]:
        assert f"  {line}" in text, line
    assert re.findall(r"quiet_(\w+): assert", text) == re.findall(r"reset_mid_(\w+): cover", text)


def test_no_cover_where_the_signal_can_never_be_active(tmp_path: Path) -> None:
    """With every sram0 region read-only, no master can write it: sram0 keeps its write
    assertions, but write covers would fail by construction, so they are left out."""
    spec = load(GOLDEN)
    ro = tuple(r.model_copy(update={"access": "RO"}) if r.slave == "sram0" else r
               for r in spec.memory_map)  # fmt: skip
    generate(spec.model_copy(update={"memory_map": ro}), tmp_path, GOLDEN.parent)
    text = (tmp_path / "formal" / "demo_ss_reset.sv").read_text(encoding="utf-8")
    for sig in ("awvalid", "wvalid", "arvalid"):
        assert f"quiet_sram0_axi_{sig}: assert" in text
    assert "reset_mid_sram0_axi_arvalid: cover" in text
    assert "reset_mid_sram0_axi_awvalid" not in text and "reset_mid_sram0_axi_wvalid" not in text


def test_a_reset_group_asserts_together_and_releases_in_any_order(tmp_path: Path) -> None:
    """demo_ss is one reset group of four domains (D51): each reset starts asserted, and one
    that newly asserts takes the whole group with it; nothing ties their release. apb4_min
    has two groups, which are independent."""
    text = _props(tmp_path, GOLDEN)
    every = "(!rst_aon_n) && (!rst_cpu_n) && (!rst_per_n) && (!rst_sys_n)"
    for r in ("rst_aon_n", "rst_cpu_n", "rst_per_n", "rst_sys_n"):
        assert f"  always_comb if (bag_init) assume (!{r});\n" in text
        assert f"  always_comb if ((!{r}) && !bag_was_{r}) assume ({every});\n" in text
    assert "==" not in text.split("// Reset group 0")[1].split("// D_AON")[0]
    two = _props(tmp_path / "two", P2 / "apb4_min.ir.yaml")
    assert "  always_comb if (bag_init) assume (rst_a);\n" in two  # active-high
    assert "  always_comb if (bag_init) assume (!rst_u_n);\n" in two
    assert "&& !bag_was_" not in two  # a group of one: nothing to assert together


def test_a_synchronized_sync_assertion_gets_the_synchronizer_stages_first(
    tmp_path: Path,
) -> None:
    """apb4_rules's reset asserts and deasserts synchronously, so the wrapper puts it through
    bag_rst_sync, which passes the assertion on after 2 edges: the domain is checked from then
    on (the generated reset test allows the same), and the reset may only be released once it
    has taken effect (D51)."""
    text = _props(tmp_path, P2 / "apb4_rules.ir.yaml")
    assert "  wire bag_quiet_D_R = bag_held_D_R && bag_edges_D_R == 2'd2;\n" in text
    release = "always_comb if (bag_was_rst_r_n && !bag_held_D_R) assume (bag_quiet_was_D_R);"
    assert f"  {release}\n" in text
    assert re.search(r"if \(bag_quiet_D_R\) quiet_\w+: assert", text)
    assert not re.search(r"if \(bag_held_D_R\) quiet_\w+: assert", text)


def test_a_sync_assertion_without_a_synchronizer_takes_effect_at_once(tmp_path: Path) -> None:
    """ahb_fixed's reset asserts synchronously but deasserts asynchronously: the wrapper wires
    it as is (only bag_rst_sync, for sync deassertion, delays an assertion), so the design
    is in reset as soon as it is asserted. Modelling 2 synchronizer edges there kept the
    protocol checkers live for 2 edges into the reset (a false failure on ahb_fixed in PR 2b)
    and started the quiet checks 2 edges late."""
    text = _props(tmp_path, P2 / "ahb_fixed.ir.yaml")
    assert "  wire bag_quiet_D_H = bag_held_D_H;\n" in text
    assert "bag_edges_D_H" not in text


@pytest.mark.parametrize("config", [c for c in CONFIGS if not _opaque(c)], ids=lambda c: c.stem)
def test_every_design_checks_something_and_covers_each_check(tmp_path: Path,
                                                            config: Path) -> None:  # fmt: skip
    """As ``checked > 0`` for a test: every design asserts something, and every assertion
    has its cover unless the route model shows its signal can never be active."""
    text = _props(tmp_path, config)
    asserted = re.findall(r"quiet_(\w+): assert", text)
    covered = set(re.findall(r"reset_mid_(\w+): cover", text))
    assert asserted and covered <= set(asserted)
    assert covered, f"{config.stem}: no cover at all"
    # And every checked protocol port has a cover of its own: in the protocol job, or in its
    # fabric's route job (D90).
    spec = load(config)
    protocol = tmp_path / "formal" / f"{spec.project.name}_protocol.sv"
    if protocol.is_file():
        texts = [protocol, *sorted((tmp_path / "formal").glob(f"{spec.project.name}_route_*.sv"))]
        found_covers = r"proto_\w+?_(\w+): cover"
        ports = {p for f in texts
                 for p in re.findall(found_covers, f.read_text(encoding="utf-8"))}  # fmt: skip
        netlist = build_netlist(spec)
        own = [*axi4_ports(netlist), *ahb_ports(netlist)]
        found = {c.port for c in checkers(spec, netlist)} | {b.name for b in own}
        assert found and all(any(p.endswith(port) for p in ports) for port in found), found
    # Step registers come from formal/bag_step.v: under yosys-slang a register on a net
    # marked (* gclk *) is clocked by a free net, not the solver's step (PR 1's bug).
    assert "gclk" not in text and "always_ff @(posedge bag_step)" not in text


@pytest.mark.parametrize("config", CONFIGS, ids=lambda c: c.stem)
def test_formal_jobs_names_every_generated_job_and_its_tasks(tmp_path: Path,
                                                             config: Path) -> None:  # fmt: skip
    """The formal corpus (D76) lists its tests from formal_jobs, without generating: it must
    name exactly the jobs generate writes, each with its tasks. A user-supplied fabric gets
    none (D8)."""
    spec = load(config)
    out = generate(spec, tmp_path, config.parent)
    found = {Path(rel).stem.removeprefix(f"{spec.project.name}_"):
             tuple(t.removeprefix(f"{Path(rel).stem}_") for t in job_tasks(tmp_path / rel))
             for rel in out.formal}  # fmt: skip
    assert dict(formal_jobs(spec, build_netlist(spec))) == found
    assert bool(found) is not _opaque(config)


@pytest.mark.parametrize("config", [c for c in CONFIGS if not _opaque(c)], ids=lambda c: c.stem)
def test_every_port_the_properties_bind_to_is_a_port_of_the_top(tmp_path: Path,
                                                                config: Path) -> None:  # fmt: skip
    """``bind ... (.*)`` connects by name: each input of a property module (quiet-in-reset,
    and port protocol where there is one) must be a port of the lint top, whatever the
    signal pattern and prefixes."""
    spec = load(config)
    name = spec.project.name
    modules = {
        f"{name}_reset": props_module(spec),
        f"{name}_protocol": f"bag_{name}_protocol_props",
    }
    for style in ("flat", "sv-interface"):
        out = generate(spec, tmp_path / style, config.parent, style)
        top = (tmp_path / style / "rtl" / f"{out.top}.sv").read_text(encoding="utf-8")
        top_ports = set(_PORT.findall(top.split(");", 1)[0]))
        for stem, module_name in modules.items():
            path = tmp_path / style / "formal" / f"{stem}.sv"
            if not path.is_file():
                assert stem.endswith("_protocol") and not checkers(spec, build_netlist(spec))
                continue
            props = path.read_text(encoding="utf-8")
            module = props.split(f"module {module_name} (", 1)[1].split(");", 1)[0]
            wanted = set(_PORT.findall(module))
            assert wanted and wanted <= top_ports, (stem, sorted(wanted - top_ports))
            assert props.endswith(f"bind {out.top} {module_name} u_{module_name} (.*);\n")


def test_the_job_reads_the_file_list_then_the_properties(tmp_path: Path) -> None:
    spec = load(GOLDEN)
    out = generate(spec, tmp_path, GOLDEN.parent)
    assert out.formal == (
        "formal/demo_ss_decode.sby",
        "formal/demo_ss_protocol.sby",
        "formal/demo_ss_reset.sby",
        "formal/demo_ss_route_ahb_bus.sby",
        "formal/demo_ss_route_aon_apb.sby",
        "formal/demo_ss_route_br_ahb2aon.sby",
        "formal/demo_ss_route_br_ahb2main.sby",
        "formal/demo_ss_route_br_main2cfg.sby",
        "formal/demo_ss_route_br_main2per.sby",
        "formal/demo_ss_route_cfg_bus.sby",
        "formal/demo_ss_route_main_xbar.sby",
        "formal/demo_ss_route_periph_apb.sby",
    )
    job = (tmp_path / "formal/demo_ss_reset.sby").read_text(encoding="utf-8")
    read = " ".join([*(f"-I {d.as_posix()}" for d in include_dirs(out)), "--top", out.top,
                     *(f.as_posix() for f in sources(out)), "demo_ss_reset.sv"])  # fmt: skip
    # The step registers (formal/bag_step.v) are read first, by Yosys's own frontend.
    assert f"\nread_verilog -formal bag_step.v\nread_slang --extern-modules {read}\n" in job
    assert f"\nprep -top {out.top}\n" in job
    assert f"prove: depth {PROVE_DEPTH}\n" in job and f"cover: depth {COVER_DEPTH}\n" in job
    assert job.endswith("[files]\nrtl ../rtl\nbag_step.v\ndemo_ss_reset.sv\n")
    assert (tmp_path / "formal" / "bag_step.v").is_file()
    assert job.startswith("# // Copyright 2026 Example Corp.\n")  # the File header, commented


def test_no_formal_for_a_user_supplied_fabric_or_when_not_asked(tmp_path: Path) -> None:
    """D8: BAG does not know how a user-supplied fabric behaves."""
    config = P3 / "external_fabric.ir.yaml"
    out = generate(load(config), tmp_path / "ext", config.parent)
    assert out.formal == () and not (tmp_path / "ext" / "formal").exists()
    spec = load(GOLDEN)
    off = spec.model_copy(update={"project": spec.project.model_copy(update={
        "outputs": spec.project.outputs.model_copy(update={"formal": False})})})  # fmt: skip
    out = generate(off, tmp_path / "off", GOLDEN.parent)
    assert out.formal == () and not (tmp_path / "off" / "formal").exists()


# ---------------------------------------------------------------- port protocol (D60)
def _protocol(tmp_path: Path, config: Path) -> str:
    spec = load(config)
    generate(spec, tmp_path, config.parent)
    path = tmp_path / "formal" / f"{spec.project.name}_protocol.sv"
    return path.read_text(encoding="utf-8")


def test_each_axi4_lite_and_apb_port_gets_the_checker_for_its_side() -> None:
    """A port that connects an external master gets the slave checker (the design is the
    slave there); a port that connects an external slave gets the master checker. AXI4 and
    AHB-Lite ports get none here (PR 2b)."""
    spec = load(GOLDEN)
    found = {c.port: c.vendor for c in checkers(spec, build_netlist(spec))}
    assert found == {
        "clkrst0": "faxil_master", "pmu0": "faxil_master", "dbg0": "faxil_slave",
        "sec0": "faxil_slave", **{p: "fapb_master" for p in
                                  ("gpio0", "rtc0", "spi0", "timer0", "uart0", "wdt0")},
    }  # fmt: skip


def test_checker_parameters_follow_the_port_and_bag_s_resets() -> None:
    """Widths from the port; async resets; no bounded-wait checks (a port's latency depends
    on slaves that may stall without limit); PSLVERR checked only where the port has it."""
    spec = load(P2 / "apb3_wide.ir.yaml")
    params = {c.port: dict(c.params) for c in checkers(spec, build_netlist(spec))}
    assert params["p3"] == {"AW": 16, "DW": 32, "F_OPT_MAXSTALL": 0, "F_OPT_SLVERR": 1,
                            "F_OPT_ASYNC_RESET": 1}  # fmt: skip
    assert params["p2"]["F_OPT_SLVERR"] == 0  # PSLVERR tied on this port
    golden = load(GOLDEN)
    dbg = next(c for c in checkers(golden, build_netlist(golden)) if c.port == "dbg0")
    assert dict(dbg.params) == {
        "C_AXI_DATA_WIDTH": 32, "C_AXI_ADDR_WIDTH": 32, "F_OPT_ASYNC_RESET": 1,
        "F_OPT_ASSUME_RESET": 1, "F_OPT_NO_RESET": 1, "F_LGDEPTH": 8, "F_AXI_MAXWAIT": 0,
        "F_AXI_MAXRSTALL": 0, "F_AXI_MAXDELAY": 0,
    }  # fmt: skip


def test_an_apb3_port_and_a_tied_signal_feed_the_checker_what_the_spec_implies(
    tmp_path: Path,
) -> None:
    """APB3 has no PSTRB or PPROT: the checker sees all strobes on writes, none on reads,
    and PPROT 0. A tied PSLVERR is the tie value. The master port's checker sees PREADY only
    with PSEL (D61: BAG holds PREADY high while PSEL is low, where the spec leaves it open)."""
    text = _protocol(tmp_path, P2 / "apb3_wide.ir.yaml")
    p0 = text.split("u_p0 (")[1].split(");")[0]
    assert ".PWSTRB((apb3_p0_pwrite ? 4'hF : 4'h0))" in p0
    assert ".PPROT(3'b000)" in p0
    assert ".PSLVERR(1'b0)" in text.split("u_p2 (")[1].split(");")[0]
    host = text.split("u_host (")[1].split(");")[0]
    assert ".PREADY((apb3_host_pready && apb3_host_psel))" in host


def test_a_checker_sees_the_reset_as_the_clock_edges_do(tmp_path: Path) -> None:
    """A checker samples its reset at clock edges. A reset an edge saw ends at its release,
    as AXI defines it. An async reset that came and went between two edges, which no edge saw
    (also one before the first edge), counts until the next edge, so the checker knows its
    handshakes were cleared."""
    text = _protocol(tmp_path, GOLDEN)
    for line in [
        "  always_ff @(posedge clk_per or posedge bag_in_reset_D_PER)",
        "    if (bag_in_reset_D_PER) bag_reset_since_edge_D_PER <= 1'b1;",
        "  logic bag_reset_seen_D_PER = 1'b0;  // no edge has seen anything yet",
        "  always_ff @(posedge clk_per) bag_reset_seen_D_PER <= bag_in_reset_D_PER;",
        "  wire bag_checker_reset_D_PER = bag_in_reset_D_PER || "
        "(bag_reset_since_edge_D_PER && !bag_reset_seen_D_PER);",
        "      .PRESETn(!bag_checker_reset_D_PER),",
    ]:
        assert f"{line}\n" in text, line


def test_masters_stay_quiet_until_the_reset_group_is_out_of_reset(tmp_path: Path) -> None:
    """Masters and slaves drive nothing while the checker sees reset, until the edge after
    it (AXI A3.1.2: a master first drives VALID at an edge after reset), and until every
    domain of the reset group has left reset (D51 step 4). demo_ss's resets deassert
    synchronously, so a domain leaves reset two edges after release. A peer still held in
    reset counts as not out (its resets release in any order): the window once looked only
    at peers released but not yet out (formal review of #46)."""
    text = _protocol(tmp_path, GOLDEN)
    group = " || ".join(f"bag_in_reset_D_{d} || bag_leaving_D_{d}"
                        for d in ("AON", "CPU", "PER", "SYS"))  # fmt: skip
    for line in [
        "  always_ff @(posedge clk_per) bag_out_edges_D_PER <= bag_in_reset_D_PER ? 2'd0 : "
        "bag_out_edges_D_PER == 2'd2 ? bag_out_edges_D_PER : bag_out_edges_D_PER + 2'd1;",
        "  assign bag_leaving_D_PER = !bag_in_reset_D_PER && bag_out_edges_D_PER != 2'd2;",
        "  wire bag_env_quiet_D_PER = bag_checker_reset_D_PER || "
        f"bag_checker_reset_was_D_PER || {group};",
    ]:
        assert f"{line}\n" in text, line


def test_each_axi4_port_gets_bags_checker_on_the_right_side(tmp_path: Path) -> None:
    """Every AXI4 port of demo_ss gets bag_faxi4, asserting what the design drives: its
    responses towards a master (cpu0), its requests towards a slave (ddr0). The checker follows
    every ID by itself (D64): no tracked ID, so an 8-bit slave ID needs nothing special. The
    2a stopgap handshake assumptions are gone."""
    text = _protocol(tmp_path, GOLDEN)
    assert ("  bag_faxi4 #(.DesignIsSlave(1), .AddrWidth(32), .DataWidth(128), .IdWidth(6)) "
            "u_cpu0 (\n      .clk_i(clk_cpu),\n      .rst_ni(!bag_checker_reset_D_CPU),\n"
            "      .awvalid(cpu0_axi_awvalid),\n") in text  # fmt: skip
    assert ("  bag_faxi4 #(.DesignIsSlave(0), .AddrWidth(32), .DataWidth(128), .IdWidth(8)) "
            "u_ddr0 (\n") in text  # fmt: skip
    assert "track" not in text and "bag_pend_" not in text


def test_an_axi4_master_port_covers_the_states_its_checker_tracks(tmp_path: Path) -> None:
    """PR 2b review (richer AXI4 covers): towards a master the environment can always make a
    multi-beat write, send data before its address, open two reads of one ID and fill a
    table, so the checker's FIFO and tables are explored, not only single beats. Towards a
    slave, an R error where reads reach, as a B error where writes do."""
    text = _protocol(tmp_path, GOLDEN)
    assert "      .full_o(bag_cpu0_full)\n  );\n" in text
    for e in ("multi_write", "w_first", "same_id", "full"):
        assert f"  always_comb proto_{e}_cpu0: cover (bag_cpu0_{e});\n" in text, e
        assert f"proto_{e}_ddr0" not in text, e
    assert ("  always_comb proto_r_error_ddr0: cover (ddr0_axi_rvalid && ddr0_axi_rready && "
            "ddr0_axi_rresp[1]);\n") in text  # fmt: skip


def test_each_ahb_port_gets_bags_checker_bound_for_its_side(tmp_path: Path) -> None:
    """PR 2b review (an AHB binding host test): on a master port (m0) the design is the slave,
    always selected, and its HREADY is the data phase's HREADYOUT; on a slave port (s0) the
    design is the master and the slave's own HSEL, HREADY and HREADYOUT are watched. A
    signal the port lacks takes the spec's value (no HMASTLOCK: 0). Towards a master the
    design's ERROR is covered too, besides the environment's burst."""
    text = _protocol(tmp_path, P2 / "ahb_fixed.ir.yaml")
    m0 = text.split(" u_m0 (\n", 1)[1].split("  );", 1)[0]
    s0 = text.split(" u_s0 (\n", 1)[1].split("  );", 1)[0]
    assert "  bag_fahb #(.DesignIsSlave(1), .AddrWidth(32), .DataWidth(32)) u_m0 (\n" in text
    assert "  bag_fahb #(.DesignIsSlave(0), .AddrWidth(32), .DataWidth(32)) u_s0 (\n" in text
    assert "      .hsel(1'b1),\n" in m0
    assert "      .hready(m0_ahb_hready),\n      .hreadyout(m0_ahb_hready),\n" in m0
    assert "      .hsel(s0_ahb_hsel),\n" in s0 and "      .hmastlock(1'b0),\n" in s0
    assert "      .hready(s0_ahb_hready),\n      .hreadyout(s0_ahb_hreadyout),\n" in s0
    labels = set(re.findall(r"(proto_\w+): cover", text))
    assert {"proto_burst_m0", "proto_error_m0", "proto_error_s0"} <= labels
    assert "proto_burst_s0" not in labels


def test_a_fixed_priority_shared_bus_names_its_masters_by_rank(tmp_path: Path) -> None:
    """CI bug (axi_shared_fixed, bus_s0_aw_routed on the unmutated design): the AXI4 shared bus
    puts its masters on the mux in priority order when the priority is fixed, so the ID prefix
    at a slave is a master's rank (masters with a lower Priority value), not its index. m0, m1
    and m2 have priorities 2, 1 and 0. PR 3's region-level check passed with either, as these
    masters decode alike; the scoreboard compared with the wrong master's requests."""
    generate(load(P3 / "axi_shared_fixed.ir.yaml"), tmp_path, P3)
    text = (tmp_path / "formal" / "axi_shared_fixed_route_bus.sv").read_text(encoding="utf-8")
    for m, rank in ((0, 2), (1, 1), (2, 0)):
        assert f"  wire sb_aw{m}_m0 = (int'(u_bus_m_awid[0][4:3]) == {rank}) && " in text, m
    generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path / "xr", P3)
    xbar = (tmp_path / "xr" / "formal" / "axi_xbar_rules_route_xbar.sv").read_text(encoding="utf-8")
    assert "  wire sb_aw2_m0 = (int'(u_xbar_m_awid[0][5:4]) == 2) && " in xbar  # a crossbar: index
    # Rank, not the priority value: with priorities 5, 9, 1 the ranks are 1, 2, 0. A
    # round-robin shared bus keeps the index (verification review 3).
    fab = next(f for f in load(P3 / "axi_shared_fixed.ir.yaml").fabrics if f.name == "bus")
    ports = [p.model_copy(update={"priority": v})
             for p, v in zip(sorted(fab.master_ports, key=lambda p: p.index), (5, 9, 1),
                             strict=True)]  # fmt: skip
    assert id_prefixes(fab.model_copy(update={"master_ports": ports})) == [1, 2, 0]
    rr = next(f for f in load(P3 / "axi_shared_rr.ir.yaml").fabrics if f.topology == "shared-bus")
    assert rr.arbitration != "fixed-priority"
    assert id_prefixes(rr) == list(range(len(rr.master_ports)))
    assert "  wire sb_ar0_m0 = (int'(u_bus_m_arid[0][4:3]) == 2) && " in text


def _rules(text: str, branch: str) -> tuple[set[str], set[str]]:
    """The checks BRANCH (gen_design_is_slave or _master) of a checker assumes and asserts,
    each found under the always block it belongs to: assumes at every solver step
    (always_comb), asserts at the clock edge (always_ff)."""
    part = text.split(f": {branch}\n", 1)[1].split("\n  end", 1)[0]
    assumed, asserted, block = set(), set(), ""
    for line in part.splitlines():
        if m := re.match(r"\s*(always_comb|always_ff|always)\b", line):
            block = m[1]
        if m := re.search(r"\bassume \((\w+)\);", line):
            assert block == "always_comb", line
            assumed.add(m[1])
        if m := re.search(r"\bassert \((\w+)\);", line):
            assert block == "always_ff", line
            asserted.add(m[1])
    # Every assume and assert of the branch was parsed: one on an expression, not a named
    # rule, would otherwise go unseen on both sides.
    assert part.count("assume (") == len(assumed) and part.count("assert (") == len(asserted)
    return assumed, asserted


@pytest.mark.parametrize(("checker", "env_only", "design_only"), [
    ("bag_faxi4.sv", {"ok_slave_cap"}, set()),
    ("bag_fahb.sv", {"ok_burst_ends", "ok_idle_wait"}, {"ok_routed"}),
])  # fmt: skip
def test_every_environment_rule_is_assumed_at_every_step(
    checker: str, env_only: set[str], design_only: set[str]
) -> None:
    """PR 2b review (every-step tests for the remaining environment rules): the two EDA tests
    show one rule each holding at every solver step; this pins it for all of them. Each rule
    asserted on the design on one side is assumed on the environment on the other, in an
    always_comb block, so what the design passes on combinationally is legal at a trace's
    last step too. The exceptions are one-sided by the spec: a slave environment's
    acceptance cap (D66), an AHB-Lite master's fixed-burst ends and waiting IDLE (assumed
    only), and the interconnect's HREADYOUT routing (asserted only)."""
    text = (Path(hdl_bag.__file__).parent / "formal" / checker).read_text(encoding="utf-8")
    env_master, design_slave = _rules(text, "gen_design_is_slave")
    env_slave, design_master = _rules(text, "gen_design_is_master")
    assert env_master and env_slave and design_master and design_slave  # the split found them
    assert env_master - env_only == design_master - design_only
    assert env_slave - env_only == design_slave - design_only
    assert env_only <= env_master | env_slave and design_only <= design_master | design_slave


def test_alias_pins_change_only_in_reset(tmp_path: Path) -> None:
    """Phase 4: alias pins are quasi-static. Formal assumes the strict form."""
    text = _protocol(tmp_path, P2 / "apb4_min.ir.yaml")
    assert "  always_ff @(posedge clk_a) bag_pin_was_alias_i <= alias_i;\n" in text
    assert (
        "  always_comb if (!(bag_in_reset_D_A)) assume (alias_i == bag_pin_was_alias_i);\n" in text
    )


def test_the_protocol_job_reads_the_checkers_then_the_design(tmp_path: Path) -> None:
    """ZipCPU's files are read by Yosys's own frontend, copied once per instance with its
    parameters, and instantiated by the slang-read property module."""
    spec = load(P2 / "apb3_wide.ir.yaml")
    out = generate(spec, tmp_path, P2)
    assert out.formal == (
        "formal/apb3_wide_decode.sby",
        "formal/apb3_wide_protocol.sby",
        "formal/apb3_wide_reset.sby",
        "formal/apb3_wide_route_apb.sby",
    )
    job = (tmp_path / "formal" / "apb3_wide_protocol.sby").read_text(encoding="utf-8")
    for line in [
        "read_verilog -formal zipcpu/fapb_master.v",
        "read_verilog -formal zipcpu/fapb_slave.v",
        "copy fapb_master bag_apb3_wide_fapb_master_p3",
        "chparam -set AW 16 -set DW 32 -set F_OPT_MAXSTALL 0 -set F_OPT_SLVERR 1 "
        "-set F_OPT_ASYNC_RESET 1 bag_apb3_wide_fapb_master_p3",
        f"bmc: depth {PROTOCOL_DEPTH}",
    ]:
        assert f"\n{line}\n" in job, line
    assert "read_slang --extern-modules " in job
    assert job.endswith(
        "[files]\nrtl ../rtl\nbag_step.v\nzipcpu vendor/zipcpu\napb3_wide_protocol.sv\n"
    )
    vendored = sorted(p.name for p in (tmp_path / "formal" / "vendor" / "zipcpu").iterdir())
    assert vendored == ["LICENSE", "SOURCE.md", "fapb_master.v", "fapb_slave.v"]


def test_the_protocol_bmc_runs_on_bitwuzla_and_everything_else_on_yices(tmp_path: Path) -> None:
    """D67: yices could not reach depth 16 on axi_xbar_rules's protocol bmc; bitwuzla can.
    Its cover tasks (one per port, D79), where bitwuzla was slower, and the reset job keep
    yices."""
    generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path, P3)
    formal = tmp_path / "formal"
    assert (
        "[engines]\nbmc: smtbmc bitwuzla\ncover_cpu: smtbmc yices\ncover_dbg: smtbmc yices\n"
        "cover_dma: smtbmc yices\ncover_mem: smtbmc yices\ncover_regs: smtbmc yices\n"
        "cover_rom: smtbmc yices\n" in (formal / "axi_xbar_rules_protocol.sby").read_text()
    )
    assert "[engines]\nsmtbmc yices\n" in (formal / "axi_xbar_rules_reset.sby").read_text()


def test_an_axi4_only_design_gets_a_protocol_job_without_zipcpu(tmp_path: Path) -> None:
    """axi_xbar_rules has only AXI4 ports: BAG's own checker, and no vendored file."""
    out = generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path, P3)
    assert out.formal == (
        "formal/axi_xbar_rules_decode.sby",
        "formal/axi_xbar_rules_protocol.sby",
        "formal/axi_xbar_rules_reset.sby",
        "formal/axi_xbar_rules_route_xbar.sby",
    )
    assert (tmp_path / "formal" / "bag_faxi4.sv").is_file()
    assert not (tmp_path / "formal" / "vendor").exists()


def test_each_checked_port_has_covers_for_what_can_cross_it(tmp_path: Path) -> None:
    """The protocol job proves it checked something: each port has covers for completed
    transfers each way the route model allows, a wait state, and (on a slave port that has
    PSLVERR) an error. ro0 is read-only and wo0 write-only in apb4_rules."""
    text = _protocol(tmp_path, P2 / "apb4_rules.ir.yaml")
    labels = set(re.findall(r"(proto_\w+): cover", text))
    assert {"proto_write_cpu", "proto_read_cpu", "proto_wait_cpu"} <= labels
    assert {"proto_read_ro0", "proto_wait_ro0"} <= labels and "proto_write_ro0" not in labels
    assert {"proto_write_wo0", "proto_wait_wo0", "proto_error_wo0"} <= labels
    assert "proto_read_wo0" not in labels and "proto_error_cpu" not in labels
    assert ("  always_comb proto_wait_dual0: cover (dual0_apb4_psel && dual0_apb4_penable && "
            "!dual0_apb4_pready);\n") in text  # fmt: skip


def test_an_axi4_lite_port_covers_stalls_and_errors(tmp_path: Path) -> None:
    """Towards a slave (clkrst0) the design's requests stall and the slave answers with an
    error; towards a master (dbg0) the design's responses stall."""
    labels = set(re.findall(r"(proto_\w+): cover", _protocol(tmp_path, GOLDEN)))
    assert {"proto_aw_stall_clkrst0", "proto_w_stall_clkrst0", "proto_ar_stall_clkrst0",
            "proto_b_error_clkrst0", "proto_r_error_clkrst0"} <= labels  # fmt: skip
    assert {"proto_b_stall_dbg0", "proto_r_stall_dbg0"} <= labels


def test_the_master_checker_sees_only_the_spec_defined_slave_response(tmp_path: Path) -> None:
    """fapb_master assumes what the slave drives: it sees PREADY only with PSEL and PSLVERR
    only in a completing access, so the slave stays free elsewhere, which keeps the fabric's
    own gating (D61) visible to the checkers on its master ports."""
    text = _protocol(tmp_path, P2 / "apb4_rules.ir.yaml")
    dual0 = text.split("u_dual0 (")[1].split(");")[0]
    assert ".PREADY((dual0_apb4_pready && dual0_apb4_psel))" in dual0
    assert (".PSLVERR((dual0_apb4_pslverr && dual0_apb4_psel && dual0_apb4_penable && "
            "dual0_apb4_pready))") in dual0  # fmt: skip


def test_a_slave_nothing_can_reach_gets_no_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its covers could never be reached (bag check warns: ACC-UNREACHABLE-SLAVE)."""
    spec = load(GOLDEN)
    monkeypatch.setattr("hdl_bag.formal_gen._active",
                        lambda _: {s.name: ({"read"} if s.name != "clkrst0" else set())
                                   for s in spec.slaves})  # fmt: skip
    ports = {c.port for c in checkers(spec, build_netlist(spec))}
    assert "clkrst0" not in ports and "pmu0" in ports


def test_an_axi4_or_ahb_slave_gets_only_the_accesses_the_ir_sends_it(tmp_path: Path) -> None:
    """PR 2b review (the unreachable-slave policy): BAG's AXI4 and AHB-Lite checkers check a
    slave port whatever the route model says, as ZipCPU's do with F_OPT_READ_ONLY: the
    design never sends a read-only slave a write (axi_shared_fixed's s1, bridges_inhouse_cdc's
    rom), sampled at the clock edge out of reset."""
    generate(load(P3 / "axi_shared_fixed.ir.yaml"), tmp_path / "sf", P3)
    sf = (tmp_path / "sf" / "formal" / "axi_shared_fixed_protocol.sv").read_text(encoding="utf-8")
    assert ("  always @(posedge clk_sys) if (!bag_checker_reset_D_SYS) "
            "proto_no_write_s1: assert (!(s1_axi_awvalid || s1_axi_wvalid));\n") in sf  # fmt: skip
    assert "proto_no_read_s1" not in sf and "proto_no_write_s0" not in sf
    generate(load(P3 / "bridges_inhouse_cdc.ir.yaml"), tmp_path / "bc", P3)
    bc = (tmp_path / "bc" / "formal" / "bridges_inhouse_cdc_protocol.sv").read_text(
        encoding="utf-8"
    )
    assert re.search(r"\) proto_no_write_rom: assert \(!\(rom_ahb_hsel && rom_ahb_htrans\[1\] "
                     r"&& rom_ahb_hwrite\)\);\n", bc)  # fmt: skip
    # Each such assertion says why it has no cover (verification review 5).
    assert "  // proto_no_write_s1: no cover, the IR sends s1 no write\n" in sf
    assert "  // proto_no_write_rom: no cover, the IR sends rom no write\n" in bc


def test_an_axi4_slave_nothing_reaches_gets_neither_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slave the route model sends nothing (ACC-UNREACHABLE-SLAVE) keeps BAG's checker,
    unlike a ZipCPU port: the design is asserted to send it no write and no read."""
    spec = load(GOLDEN)
    monkeypatch.setattr("hdl_bag.formal_gen._active",
                        lambda _: {s.name: ({"read", "write"} if s.name != "ddr0" else set())
                                   for s in spec.slaves})  # fmt: skip
    text = _protocol(tmp_path, GOLDEN)
    assert " u_ddr0 (\n" in text
    assert "proto_no_write_ddr0: assert (!(ddr0_axi_awvalid || ddr0_axi_wvalid));\n" in text
    assert "proto_no_read_ddr0: assert (!(ddr0_axi_arvalid));\n" in text


def test_a_master_behind_an_aw_spill_never_gets_its_data_taken_first(tmp_path: Path) -> None:
    """D81, nightly 43207958073: proto_w_first was unreached on 7 AXI4 masters wired straight
    into a bag_axi_xbar with LatencyMode CUT_SLV_AX or CUT_ALL_AX. axi_demux_simple routes W
    once AWVALID is presented, so a bare crossbar port can take W first (axi_xbar_pow2,
    NO_LATENCY, reached it); behind an AW spill register the port's AW waits only while the
    register holds earlier AWs, whose data comes first. There the bmc asserts W never comes
    before its AW instead. A CDC, FIFO or cut in front takes W on its own channel."""
    xr = _protocol(tmp_path / "xr", P3 / "axi_xbar_rules.ir.yaml")  # CUT_ALL_AX
    for m in ("cpu", "dbg", "dma"):
        assert (f"  always @(posedge clk_sys) if (!bag_checker_reset_D_SYS) "
                f"proto_no_w_first_{m}: assert (!bag_{m}_w_first);\n") in xr  # fmt: skip
        assert f"proto_w_first_{m}: cover" not in xr
        assert (f"  // proto_no_w_first_{m}: no cover, {m} enters xbar's AW spill register "
                "(CUT_ALL_AX)\n") in xr  # fmt: skip
    assert "proto_w_first_a: cover" in _protocol(tmp_path / "xp", P3 / "axi_xbar_pow2.ir.yaml")
    ad = _protocol(tmp_path / "ad", P3 / "axi_adapters.ir.yaml")  # CUT_SLV_AX, CDC and FIFO first
    assert "proto_w_first_cpu: cover" in ad and "proto_w_first_dma: cover" in ad
    assert "proto_no_w_first" not in ad


def test_a_master_through_a_one_at_a_time_lite_converter_opens_one_read(tmp_path: Path) -> None:
    """D80 (owner: keep the RTL, document the limit): mixed_lite_shared's dbg enters
    bag_axi_to_axil, PULP axi_to_axi_lite with MaxTrans 2, whose burst splitter's
    axi_demux_simple has 1-bit ID counters (idx_width(2)), full at 1, any full blocking every
    ID: one read and one write at a time, fewer than configured. proto_same_id_dbg was
    unreached (nightly 43207958073); the bmc asserts the limit instead."""
    ls = _protocol(tmp_path, P3 / "mixed_lite_shared.ir.yaml")
    assert ("  always @(posedge clk_sys) if (!bag_checker_reset_D_SYS) "
            "proto_no_same_id_dbg: assert (!bag_dbg_same_id);\n") in ls  # fmt: skip
    assert "proto_same_id_dbg: cover" not in ls
    assert ("  // proto_no_same_id_dbg: no cover, dbg enters dbg_pc_2_cfg, PULP axi_to_axi_lite "
            "with MaxTrans 2: one read at a time (D80)\n") in ls  # fmt: skip
    # Nor can four reads or four unanswered writes be open (a local cover run, D82, left
    # proto_full_dbg unreached).
    assert ("  always @(posedge clk_sys) if (!bag_checker_reset_D_SYS) "
            "proto_no_full_dbg: assert (!bag_dbg_full);\n") in ls  # fmt: skip
    assert "proto_full_dbg: cover" not in ls
    assert "proto_w_first_dbg: cover" in ls


def test_a_one_at_a_time_lite_converter_further_along_still_limits_the_port(
    tmp_path: Path,
) -> None:
    """D91 (owner, 2026-11-14, extends D80): axil_adapters' cpu goes through bag_dw (64 to 32
    bits) and then bag_pc to AXI4-Lite (PULP axi_to_axi_lite, MaxTrans 2) before its fabric;
    proto_same_id_cpu and proto_full_cpu went unreached for 3 hours (run 43381018017). Such a
    converter anywhere between a master and its fabric limits the port as when it is first.
    proto_w_first_cpu too: a downsizing bag_axi_dw holds W until it has taken the AW."""
    aa = _protocol(tmp_path, P3 / "axil_adapters.ir.yaml")
    for e in ("same_id", "full", "w_first"):
        assert f"proto_no_{e}_cpu: assert (!bag_cpu_{e});" in aa, e
        assert f"proto_{e}_cpu: cover" not in aa, e
    assert ("  // proto_no_same_id_cpu: no cover, cpu reaches cpu_pc_2_cfg, PULP "
            "axi_to_axi_lite with MaxTrans 2: one read at a time (D80, D91)\n") in aa  # fmt: skip
    assert ("  // proto_no_w_first_cpu: no cover, cpu enters cpu_dw_2_cfg, a downsizing "
            "bag_axi_dw: W waits for its AW (D91)\n") in aa  # fmt: skip
    assert "proto_multi_write_cpu: cover" in aa


def test_an_apb_port_behind_a_crossing_bridge_is_covered_by_its_fabric_unit(
    tmp_path: Path,
) -> None:
    """D90 (owner, 2026-11-14): demo_ss's gpio0, spi0, timer0 and uart0 cover tasks reached
    none of their covers by step 27 in 3 hours (run 43381018017): cpu0 reaches them through a
    cut, the AXI crossbar and an AXI4-to-APB bridge across clock domains. A slave port of an
    APB fabric whose masters all come through bridges between clock domains gets its protocol
    covers in that fabric's route unit, which holds the same checker on the same port alone;
    the protocol job keeps its checker (the bmc) and drops its cover task. aon_apb's ports,
    behind a bridge within one domain, keep theirs."""
    ds = _protocol(tmp_path, GOLDEN)
    job = (tmp_path / "formal" / "demo_ss_protocol.sby").read_text(encoding="utf-8")
    route = (tmp_path / "formal" / "demo_ss_route_periph_apb.sv").read_text(encoding="utf-8")
    for p in ("gpio0", "spi0", "timer0", "uart0"):
        assert f"\ncover_{p}\n" not in job, p
        assert f"proto_read_{p}: cover" not in ds, p
        assert f"  // {p}: covers in route_periph_apb (D90)\n" in ds, p
        # At the clock edge, out of reset, as every route property.
        assert re.search(rf"\n  always @\(posedge \w+\) proto_read_{p}: cover \(bag_live_D_PER "
                         rf"&& {p}_apb_psel && ", route), p  # fmt: skip
        assert f"BAG_IDLE_{p}" not in ds, p
    assert "\ncover_wdt0\n" in job and "proto_read_wdt0: cover" in ds
    assert "proto_read_wdt0" not in route


def test_a_cover_task_holds_the_other_masters_idle(tmp_path: Path) -> None:
    """D82 (owner, 2026-11-13): 33 per-port cover tasks ran past an hour, each holding the
    whole design. A master port's cover task assumes every other master idle; a slave port's
    keeps, for each access the IR sends it, only the nearest master making it (ties by name).
    A trace with idle masters is legal in the full design, so a cover it reaches still counts;
    the bmc keeps every master free."""
    xr = _protocol(tmp_path, P3 / "axi_xbar_rules.ir.yaml")
    # Each block ends on a line of its own (the last one once ran into `endmodule`).
    assert "`endif\nendmodule\n" in xr and "`endifendmodule" not in xr
    blocks = dict(re.findall(r"`ifdef BAG_IDLE_(\w+)\n(.*?)`endif", xr, re.S))
    idle = {p: sorted(re.findall(r"bag_idle_(\w+)_for_\w+: assume", b)) for p, b in blocks.items()}
    # Master ports enter the crossbar's arbiter, so each keeps one competitor (D86).
    assert idle == {"cpu": ["dma"], "dbg": ["dma"], "dma": ["dbg"],
                    "mem": ["dbg", "dma"], "regs": ["dbg"], "rom": ["dma"]}  # fmt: skip
    # regs: cpu only reads it, so dma (the one writer) stays too; rom: cpu reads it (ahead
    # of dbg by name), dbg is its only writer.
    assert ("  always_comb bag_idle_dma_for_cpu: assume (!(dma_axi_awvalid || dma_axi_wvalid "
            "|| dma_axi_arvalid));\n") in xr  # fmt: skip
    job = (tmp_path / "formal" / "axi_xbar_rules_protocol.sby").read_text(encoding="utf-8")
    assert "\ncover_rom: read_slang -D BAG_COVER_rom -D BAG_IDLE_rom --extern-modules " in job
    assert "\nbmc: read_slang --extern-modules " in job


def test_a_master_entering_an_arbiter_keeps_one_competitor_in_its_cover_task(
    tmp_path: Path,
) -> None:
    """D86 (owner, 2026-11-13, amends D82): on the in-house shared bus a master's AW waits,
    so its W can go first, only while another master holds the bus; with every other master
    idle, axi_shared_fixed's proto_w_first_m0..m2 went unreached (nightly 43328525704). A
    master port whose first component is a fabric with more than one master keeps the
    nearest competing master free (path cost, then name). One master on its fabric: none."""
    sf = _protocol(tmp_path / "f", P3 / "axi_shared_fixed.ir.yaml")
    blocks = dict(re.findall(r"`ifdef BAG_IDLE_(\w+)\n(.*?)`endif", sf, re.S))
    idle = {p: sorted(re.findall(r"bag_idle_(\w+)_for_\w+: assume", b)) for p, b in blocks.items()}
    assert {p: idle[p] for p in ("m0", "m1", "m2")} == {"m0": ["m2"], "m1": ["m2"], "m2": ["m1"]}
    assert "  // D82: m0's cover task; m0, m1 active, the other masters idle\n" in sf
    ds = _protocol(tmp_path / "d", GOLDEN)
    blocks = dict(re.findall(r"`ifdef BAG_IDLE_(\w+)\n(.*?)`endif", ds, re.S))
    # mcu0 is the AHB-Lite bus's one master; cpu0 enters a register slice first.
    assert "bag_idle_cpu0_for_mcu0" in blocks["mcu0"] and "bag_idle_dbg0_for_cpu0" in blocks["cpu0"]


def test_each_port_covers_at_its_own_path_depth_and_the_bmc_at_16(tmp_path: Path) -> None:
    """D77, D79: the bmc runs at PROTOCOL_DEPTH; each checked port's covers run in a task of
    their own, at its path depth (never below 16), reading only that port's covers: its macro
    keeps BAG's, and chformal drops those inside the other ports' ZipCPU checkers. On
    bridges_inhouse (one domain, one clock) spi sits behind three bridges from the nearest
    master that reaches it: 8 edges for one component and 4 for each of the other four."""
    assert (PROTOCOL_DEPTH, PATH_EDGES, PATH_EDGES_PER_HOP) == (16, 8, 4)
    generate(load(P2 / "apb4_rules.ir.yaml"), tmp_path / "a", P2)
    job = (tmp_path / "a" / "formal" / "apb4_rules_protocol.sby").read_text(encoding="utf-8")
    assert "[tasks]\nbmc\ncover_cpu\ncover_dual0\ncover_ro0\ncover_sec0\ncover_wo0\n\n" in job
    assert f"bmc: depth {PROTOCOL_DEPTH}\ncover_cpu: mode cover\ncover_cpu: depth 16\n" in job
    assert "\ncover_ro0: read_slang -D BAG_COVER_ro0 -D BAG_IDLE_ro0 --extern-modules " in job
    assert "\nbmc: read_slang --extern-modules " in job
    assert ("cover_cpu: chformal -remove -cover bag_apb4_rules_fapb_master_dual0 "
            "bag_apb4_rules_fapb_master_ro0 bag_apb4_rules_fapb_master_sec0 "
            "bag_apb4_rules_fapb_master_wo0\n") in job  # fmt: skip
    props = (tmp_path / "a" / "formal" / "apb4_rules_protocol.sv").read_text(encoding="utf-8")
    assert "`ifdef BAG_COVER_ro0\n  always_comb proto_read_ro0: cover (" in props
    # Every cover sits inside its port's macro, and nothing else does.
    inside = re.findall(r"`ifdef BAG_COVER_\w+\n(.*?)`endif", props, re.S)
    assert inside and all(": cover (" in line for b in inside for line in b.splitlines())
    assert props.count(": cover (") == sum(b.count(": cover (") for b in inside)
    generate(load(P3 / "bridges_inhouse.ir.yaml"), tmp_path / "b", P3)
    job = (tmp_path / "b" / "formal" / "bridges_inhouse_protocol.sby").read_text(encoding="utf-8")
    assert "cover_spi: mode cover\ncover_spi: depth 24\n" in job
    assert "cover_cpu: mode cover\ncover_cpu: depth 16\n" in job
    assert f"bmc: mode bmc\nbmc: depth {PROTOCOL_DEPTH}\n" in job


def test_the_decode_harness_checks_the_package_decoder_against_the_ir(tmp_path: Path) -> None:
    """D69: the harness feeds the fabric's package tables to bag_addr_decode and compares
    its outputs with a reference written from the IR as explicit ranges, so a wrong table
    entry shows. On apb4_rules, ro0's region takes reads (port 0) and refuses writes (the
    error, 4 = the slave count); the job is a one-step prove."""
    spec = load(P2 / "apb4_rules.ir.yaml")
    out = generate(spec, tmp_path, P2)
    text = (tmp_path / "formal" / "apb4_rules_decode.sv").read_text(encoding="utf-8")
    assert "module bag_apb4_rules_decode (" in text
    assert re.search(r"\n      \.RuleBase +\(apb4_rules_pkg::ApbRuleBase\),\n", text)
    assert "  function automatic int unsigned apb_target(int unsigned m, " in text
    assert ("      if (sys >= 65'(64'h0000_0000_1000_0000) && "
            "sys < 65'(64'h0000_0000_1000_1000))  // ro0.regs\n"
            "        case ({write, nonsec}) 2'b00: return 0; 2'b01: return 0; "
            "2'b10: return 4; 2'b11: return 4; endcase\n") in text  # fmt: skip
    for label in ("apb_selects", "apb_errors", "apb_aliases"):
        assert f"    {label}: assert (" in text
    job = (tmp_path / "formal" / "apb4_rules_decode.sby").read_text(encoding="utf-8")
    assert "prove: mode prove\nprove: depth 1\nmulticlock off\n" in job
    assert "read_slang --top bag_apb4_rules_decode " in job
    assert "formal/apb4_rules_decode.sby" in out.formal


def test_the_decode_reference_aliases_and_answers_misses_as_the_ir_says(tmp_path: Path) -> None:
    """The alias and miss-response parts of the reference. ahb_fixed: m1's boot alias, while
    boot_a_i is high, sends its first 4 KiB to s1's base. axi_xbar_rules: a master without a
    default slave is answered from MissResp: dma SLVERR (2'b10), cpu DECERR (2'b11)."""
    generate(load(P2 / "ahb_fixed.ir.yaml"), tmp_path / "af", P2)
    text = (tmp_path / "af" / "formal" / "ahb_fixed_decode.sv").read_text(encoding="utf-8")
    assert ("    if (m == 1 && boot_a_i && sys >= 65'(64'h0000_0000_0000_0000) && "
            "sys < 65'(64'h0000_0000_0000_1000))  // m1\n"
            "      return 32'(65'(64'h0000_0000_2001_0000) + (sys - 65'(64'h0000_0000_0000_0000)) "
            "- 65'(64'h0000_0000_0000_0000));\n") in text  # fmt: skip
    assert "    ahb_aliases: assert (ahb_dec_addr == ahb_ref_addr);\n" in text
    generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path / "xr", P3)
    text = (tmp_path / "xr" / "formal" / "axi_xbar_rules_decode.sv").read_text(encoding="utf-8")
    for m, resp, master in ((0, "2'b10", "dma"), (1, "2'b11", "cpu")):
        assert (
            f"    if (xbar_tgt == 3 && xbar_mst[{m}]) xbar_miss_response_{m}: assert "
            f"(axi_xbar_rules_pkg::XbarMissResp[{m}] == {resp});  // {master}\n"
        ) in text


def test_route_properties_are_sampled_at_the_clock_edge(tmp_path: Path) -> None:
    """CI bug (axil_shared_fixed): the checkers constrain the environment at clock edges
    only, so between edges a free clock lets a waiting master's address take any value and
    return by the next edge. A request counts where its slave samples it, at the edge, so
    every route property and its cover is clocked: an always_comb check failed on such a
    between-edge address the shared bus passed through. Bridge windows are sampled at the
    downstream clock (bridges_pulp's br_per crosses to D_PER)."""
    generate(load(P3 / "axil_shared_fixed.ir.yaml"), tmp_path / "asf", P3)
    text = (tmp_path / "asf" / "formal" / "axil_shared_fixed_route_bus.sv").read_text(
        encoding="utf-8"
    )
    assert ("  always @(posedge clk_sys) if (bag_live_D_SYS && s0_axil_awvalid) "
            "bus_s0_aw_routed: assert (") in text  # fmt: skip
    assert ("  always @(posedge clk_sys) cover_bus_s0_aw_routed: "
            "cover (bag_live_D_SYS && s0_axil_awvalid);\n") in text  # fmt: skip
    assert "  always @(posedge clk_sys) if (bag_live_D_SYS && (bag_race_aw_" in text
    # CI (axi_shared_fixed): the bus may grant the winner in the cycle both start, before the
    # race is registered, so the cover takes a race that starts now too.
    generate(load(P3 / "axi_shared_fixed.ir.yaml"), tmp_path / "sf", P3)
    sf = (tmp_path / "sf" / "formal" / "axi_shared_fixed_route_bus.sv").read_text(encoding="utf-8")
    assert ("  always @(posedge clk_sys) cover_bus_ar_m2_before_m0: cover (bag_live_D_SYS && "
            "(bag_race_ar_2_0 || (bag_ar2_start && bag_ar0_start && bag_ar2_cont && "
            "bag_ar0_cont)) && bag_ar2_won);\n") in sf  # fmt: skip
    generate(load(P3 / "bridges_pulp.ir.yaml"), tmp_path / "bp", P3)
    for path in sorted((tmp_path / "bp" / "formal").glob("*_route_*.sv")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"always_comb (if \([^\n]*\) )?\w+: (assert|cover) \(", text), path
    text = (tmp_path / "bp" / "formal" / "bridges_pulp_route_br_per.sv").read_text(encoding="utf-8")
    assert "  always @(posedge clk_per) if (bag_live_D_SYS && bag_live_D_PER && " in text


def test_a_channel_the_ir_never_uses_keeps_its_assertion_and_has_no_cover(tmp_path: Path) -> None:
    """CI bug (axil_shared_fixed): s1's only region is read-only, so the fabric reference
    sends it no write. Its AW check then says no write ever arrives, with no cover to reach,
    and its ZipCPU checker is read-only (as in the protocol job): the covers of a write
    there were unreachable by design."""
    out = generate(load(P3 / "axil_shared_fixed.ir.yaml"), tmp_path, P3)
    text = (tmp_path / "formal" / "axil_shared_fixed_route_bus.sv").read_text(encoding="utf-8")
    assert " bus_s1_aw_routed: assert (" in text
    assert "cover_bus_s1_aw_routed" not in text
    assert "  // bus_s1_aw_routed: no cover, the IR sends s1 no write\n" in text
    assert "cover_bus_s1_ar_routed: cover (" in text and "cover_bus_s0_aw_routed: cover (" in text
    job = (tmp_path / "formal" / "axil_shared_fixed_route_bus.sby").read_text(encoding="utf-8")
    s1 = next(line for line in job.splitlines()
              if line.startswith("chparam") and line.endswith("_bus_s1"))  # fmt: skip
    assert " -set F_OPT_READ_ONLY 1 " in s1
    assert "formal/axil_shared_fixed_route_bus.sby" in out.formal


def test_a_unit_in_one_clock_domain_runs_on_one_clock(tmp_path: Path) -> None:
    """CI: axi_xbar_rules's crossbar job reached step 7 of 16 in 20 minutes with free clocks,
    which cost two solver steps per edge and the logic that models them. A unit in one domain
    has no clock ratio to explore: it runs with multiclock off for ROUTE_CYCLES edges, and 2
    more per halving of a bridge's data width (each narrow transfer is another APB access: an
    AXI4-Lite to 8- or 16-bit APB bridge's read response came at edge 8). At 12 edges
    mixed_cdc_chain's crossbar reached only edge 10 in 20 minutes. A bridge across domains
    keeps free clocks, ROUTE_DEPTH steps and 6 more per halving after the first (measured:
    mixed_cdc_chain's br_per, no conversion, last cover at step 21; bridges_pulp's br_per, 64 to
    32 bits, passed with every cover at 24 and timed out at 28; mixed_narrow_apb's br_c8, 64
    to 8 bits, last cover at step 33)."""
    assert (ROUTE_CYCLES, ROUTE_DEPTH) == (8, 24)
    generate(load(P3 / "bridges_pulp.ir.yaml"), tmp_path / "bp", P3)
    formal = tmp_path / "bp" / "formal"
    xbar = (formal / "bridges_pulp_route_xbar.sby").read_text(encoding="utf-8")
    assert "bmc: depth 8\ncover: mode cover\ncover: depth 8\nmulticlock off\n" in xbar
    cdc = (formal / "bridges_pulp_route_br_per.sby").read_text(encoding="utf-8")  # 64 to 32
    assert "bmc: depth 24\ncover: mode cover\ncover: depth 24\nmulticlock on\n" in cdc
    generate(load(P3 / "mixed_narrow_apb.ir.yaml"), tmp_path / "mn", P3)
    mn = tmp_path / "mn" / "formal"
    for unit, depth, clocks in (("br_c8", 36, "on"), ("br_l8", 12, "off"), ("br_l16", 10, "off"),
                                ("br_lite", 8, "off")):  # fmt: skip
        job = (mn / f"mixed_narrow_apb_route_{unit}.sby").read_text(encoding="utf-8")
        want = f"bmc: depth {depth}\ncover: mode cover\ncover: depth {depth}\nmulticlock {clocks}\n"
        assert want in job, unit


def test_a_bridge_with_a_fifo_in_one_clock_domain_gets_two_more_edges(tmp_path: Path) -> None:
    """Nightly 43207958073: mixed_lite_shared's br_ahb (AXI4-Lite to AHB-Lite, same width,
    FIFO depth 2) left its upstream B handshake unreached in 8 edges; at 16 it came at step 9.
    The bridge's FIFO holds the request and the response an edge each, so a single-domain
    bridge with a FIFO gets ROUTE_CYCLES_PER_FIFO more edges. br_per, without one, keeps 8."""
    assert ROUTE_CYCLES_PER_FIFO == 2
    generate(load(P3 / "mixed_lite_shared.ir.yaml"), tmp_path / "ls", P3)
    formal = tmp_path / "ls" / "formal"
    for unit, depth in (("br_ahb", 10), ("br_per", 8)):
        job = (formal / f"mixed_lite_shared_route_{unit}.sby").read_text(encoding="utf-8")
        want = f"bmc: depth {depth}\ncover: mode cover\ncover: depth {depth}\nmulticlock off\n"
        assert want in job, unit


def test_an_axi4_lite_bridge_with_a_fifo_across_domains_gets_34_steps(tmp_path: Path) -> None:
    """Nightly 43047322627 and 43328525704: bridges_pulp_cdc's br_per (AXI4-Lite 64 bits to
    APB4 32 across domains, FIFO depth 2) left its upstream B and R handshakes unreached at
    24 steps; at 44 they came at steps 33 (local run, 16 s). mixed_narrow_apb's br_c8 (the
    same with 8-bit APB) also reached its last cover at 33. Such a unit gets at least
    ROUTE_DEPTH_LITE_FIFO steps; AXI4 bridges with a FIFO (bridges_pulp's br_per, all covers
    at 24) and AXI4-Lite ones without (mixed_cdc_chain's br_per, 21) keep their depths."""
    assert ROUTE_DEPTH_LITE_FIFO == 34
    cases = (("bridges_pulp_cdc", "br_per", 34), ("mixed_narrow_apb", "br_c8", 36),
             ("bridges_pulp", "br_per", 24), ("mixed_cdc_chain", "br_per", 24))  # fmt: skip
    for config, unit, depth in cases:
        generate(load(P3 / f"{config}.ir.yaml"), tmp_path / config, P3)
        job = (tmp_path / config / "formal" / f"{config}_route_{unit}.sby").read_text(
            encoding="utf-8"
        )
        want = f"bmc: depth {depth}\ncover: mode cover\ncover: depth {depth}\nmulticlock on\n"
        assert want in job, (config, unit)


def test_a_design_in_one_clock_domain_checks_its_ports_on_one_clock(tmp_path: Path) -> None:
    """Formal corpus run 43047322627: with free clocks the protocol job's 16 steps were about 8
    clock edges, and single-domain designs (mixed_ahb_secure, mixed_lite_shared, bridges_inhouse
    and others) left covers unreached. A design in one domain has no clock ratio to explore, so
    its protocol job runs on one clock, as a single-domain route job does: 16 steps, 16 edges.
    A design with several domains keeps free clocks."""
    generate(load(P3 / "mixed_ahb_secure.ir.yaml"), tmp_path / "one", P3)
    one = tmp_path / "one" / "formal" / "mixed_ahb_secure_protocol.sby"
    job = one.read_text(encoding="utf-8")
    assert "cover_tcm: depth 16\nmulticlock off\n" in job
    generate(load(P3 / "axil_adapters.ir.yaml"), tmp_path / "two", P3)
    job = (tmp_path / "two" / "formal" / "axil_adapters_protocol.sby").read_text(encoding="utf-8")
    assert "cover_mem: depth 48\nmulticlock on\n" in job


def test_a_bridge_in_one_domain_may_forward_the_request_it_is_presented(
    tmp_path: Path,
) -> None:
    """Local runs: br_lp (bridges_pulp, AXI4-Lite to APB, PULP's request path a fall-through
    register) raised PSEL in the cycle it accepted the AR, before the harness had registered
    the access; br_lite (mixed_narrow_apb, AXI4 to AXI4-Lite, a pass-through) raised the
    downstream AWVALID while the upstream AW still waited for AWREADY. Both are legal. In one
    domain the window check takes the upstream request being presented (valid, accepted or
    not) at its own address; across domains (br_per) nothing passes the CDC before the
    upstream handshake, so only a registered access counts."""
    generate(load(P3 / "bridges_pulp.ir.yaml"), tmp_path, P3)
    formal = tmp_path / "formal"
    lp = (formal / "bridges_pulp_route_br_lp.sv").read_text(encoding="utf-8")
    assert "  wire bag_up_req = (cfg_2_br_lp_axil_awvalid) || (cfg_2_br_lp_axil_arvalid);\n" in lp
    assert ("  wire [31:0] bag_win_addr = bag_up_req ? ((cfg_2_br_lp_axil_awvalid) ? "
            "cfg_2_br_lp_axil_awaddr : cfg_2_br_lp_axil_araddr) : bag_up_addr;\n"
            ) in lp  # fmt: skip
    assert "br_lp_p_in_window: assert ((bag_busy || bag_up_req) && ((br_lp_2_lp_apb3_paddr ^ " in lp
    assert "32'(65'(bag_win_addr) + " in lp
    per = (formal / "bridges_pulp_route_br_per.sv").read_text(encoding="utf-8")
    assert "br_per_p_in_window: assert (bag_busy && ((br_per_2_per_apb4_paddr ^ " in per
    assert "bag_up_req" not in per


def test_a_bridge_route_job_sees_only_the_addresses_its_upstream_fabric_sends_it(
    tmp_path: Path,
) -> None:
    """Formal corpus run 43047322627: bridges_inhouse_cdc's br_aon (AHB-Lite, 24-bit address,
    to AXI4, 32-bit) failed br_aon_aw_in_window for upstream address 0x07_5000, which the ahb
    fabric never sends it (br_aon's region is 0x4010_0000 to 0x4010_FFFF, the fabric's
    address 0 being 0x4000_0000). The bridge subtracts its offset at 24 bits and the route
    model's translation wraps at 32, so they differ only outside the window. The environment
    presents only addresses in the bridge's regions on its upstream fabric; a bridge that is a
    default slave there would take misses at any address and gets no such assumption."""
    generate(load(P3 / "bridges_inhouse_cdc.ir.yaml"), tmp_path, P3)
    text = (tmp_path / "formal" / "bridges_inhouse_cdc_route_br_aon.sv").read_text(encoding="utf-8")
    assert ("  always_comb if (ahb_2_br_aon_ahb_hsel && ahb_2_br_aon_ahb_htrans[1]) assume "
            "((ahb_2_br_aon_ahb_haddr >= 24'h10_0000 && ahb_2_br_aon_ahb_haddr <= 24'h10_FFFF));\n"
            ) in text  # fmt: skip
    spec = load(P3 / "bridges_inhouse_cdc.ir.yaml")
    fab = next(f for f in spec.fabrics if f.name == "ahb")
    decode = tuple(d.model_copy(update={"default_slave": "br_aon"}) for d in fab.decode)
    fabrics = tuple(
        f.model_copy(update={"decode": decode}) if f is fab else f for f in spec.fabrics
    )
    generate(spec.model_copy(update={"fabrics": fabrics}), tmp_path / "ds", P3)
    text = (tmp_path / "ds" / "formal" / "bridges_inhouse_cdc_route_br_aon.sv").read_text(
        encoding="utf-8")  # fmt: skip
    assert "assume ((ahb_2_br_aon_ahb_haddr >=" not in text
    assert "br_aon_aw_in_window: assert" in text


def test_an_axi4_read_is_outstanding_until_its_last_beat(tmp_path: Path) -> None:
    """Review weakness: the D70 race on AR is armed only with nothing outstanding, so the
    count drops once per read, at RLAST, not once per beat (a 4-beat read left it wrapped
    and the race disarmed for the rest of the trace). AXI4-Lite has one beat per read."""
    generate(load(P3 / "axi_shared_fixed.ir.yaml"), tmp_path, P3)
    text = (tmp_path / "formal" / "axi_shared_fixed_route_bus.sv").read_text(encoding="utf-8")
    assert ("bag_ar0_out <= bag_ar0_out + 8'(bag_ar0_acc) - "
            "8'(m0_axi_rvalid && m0_axi_rready && m0_axi_rlast);") in text  # fmt: skip
    assert ("bag_aw0_out <= bag_aw0_out + 8'(bag_aw0_acc) - "
            "8'(m0_axi_bvalid && m0_axi_bready);") in text  # fmt: skip


def test_an_axi4_bus_without_ids_feeds_its_valids_to_the_checker(tmp_path: Path) -> None:
    """Review bug: on an ID-less AXI4 bus only the ID signals are tied to 0 at the checker.
    Matching names that end in "id" tied every *valid too, so the checker assumed nothing
    about the master behind bridges_pulp's dbg_pc_2_xbar and asserted nothing about it."""
    generate(load(P3 / "bridges_pulp.ir.yaml"), tmp_path, P3)
    text = (tmp_path / "formal" / "bridges_pulp_route_xbar.sv").read_text(encoding="utf-8")
    chk = text.split(" u_chk_dbg_pc_2_xbar (\n", 1)[1].split("  );", 1)[0]
    for ch in ("aw", "w", "b", "ar", "r"):
        assert f"      .{ch}valid(dbg_pc_2_xbar_axi_{ch}valid),\n" in chk, ch
    for sig in ("awid", "bid", "arid", "rid"):
        assert f"      .{sig}(1'b0)," in chk, sig


@pytest.mark.parametrize("ir", CONFIGS, ids=lambda p: p.stem.removesuffix(".ir"))
def test_every_property_label_is_unique_and_every_assertion_has_its_cover(
    tmp_path: Path, ir: Path
) -> None:
    """In each decode and route harness, labels are unique (a duplicate is an SV error that
    names the wrong property in a report), and each route assertion has the cover of its
    condition (cover_<label>), so none holds only because its condition never comes."""
    out = generate(load(ir), tmp_path, ir.parent)
    texts = [(tmp_path / f).with_suffix(".sv").read_text(encoding="utf-8")
             for f in out.formal if "_route_" in f or f.endswith("_decode.sby")]  # fmt: skip
    for text in texts:
        for module in text.split("\nmodule ")[1:]:
            asserts = re.findall(r"(\w+): assert \(", module)
            covers = re.findall(r"(\w+): cover \(", module)
            labels = asserts + covers
            assert len(labels) == len(set(labels)), sorted(labels)
            if "_unit_" in module.split("(", 1)[0]:
                # A channel the IR never uses says so instead (no cover can be reached).
                bare = set(re.findall(r"\n  // (\w+): no cover, the IR sends ", module))
                assert asserts, module[:60]
                assert {f"cover_{a}" for a in asserts if a not in bare} <= set(covers), module[:60]
                assert bare <= set(asserts) and not {f"cover_{a}" for a in bare} & set(covers)


def test_the_route_harness_checks_each_fabric_and_bridge_alone(tmp_path: Path) -> None:
    """D69 parts 2 and 3, D70: each unit in its own harness and its own job, so no unit's
    proof waits on another's. A fabric's slave-port check reads the address on the fabric
    side, before it is fitted to the slave's width (D29); ahb_fixed's fixed priorities give
    one race property per ordered pair of masters."""
    spec = load(P2 / "apb4_rules.ir.yaml")
    generate(spec, tmp_path / "apb", P2)
    text = (tmp_path / "apb" / "formal" / "apb4_rules_route_apb.sv").read_text(encoding="utf-8")
    assert "module bag_apb4_rules_unit_apb (" in text
    assert "apb_wo0_p_routed: assert (sb_p0_m1);" in text
    assert ("  wire sb_p0_m1 = 1'b1 && (sb_p0_hd1_has ? (sb_p0_addr[sb_p0_hd1] == "
            "32'(u_apb_m_paddr[1]) && ") in text  # fmt: skip
    spec = load(P2 / "ahb_fixed.ir.yaml")
    generate(spec, tmp_path / "ahb", P2)
    text = (tmp_path / "ahb" / "formal" / "ahb_fixed_route_ahb.sv").read_text(encoding="utf-8")
    labels = re.findall(r"(ahb_a_m\d_before_m\d): assert", text)
    assert sorted(labels) == ["ahb_a_m0_before_m1", "ahb_a_m2_before_m0", "ahb_a_m2_before_m1"]
    job = (tmp_path / "ahb" / "formal" / "ahb_fixed_route_ahb.sby").read_text(encoding="utf-8")
    assert " --top bag_ahb_fixed_unit_ahb " in job
    assert "\nprep -top bag_ahb_fixed_unit_ahb\n" in job
    # One clock domain: one clock, ROUTE_CYCLES edges.
    assert ROUTE_CYCLES == 8
    assert ("bmc: mode bmc\nbmc: depth 8\ncover: mode cover\ncover: depth 8\n"
            "multiclock off\n") in job  # fmt: skip
    # On an AXI4 crossbar, the master is the ID's top bits (dma, cpu, dbg: 2 of 6).
    generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path / "xr", P3)
    xbar = (tmp_path / "xr" / "formal" / "axi_xbar_rules_route_xbar.sv").read_text(encoding="utf-8")
    assert "xbar_regs_aw_routed: assert (sb_aw0_m0 || sb_aw1_m0 || sb_aw2_m0);" in xbar
    assert "  wire sb_aw1_m0 = (int'(u_xbar_m_awid[0][5:4]) == 1) && (sb_aw1_hd0_has ? " in xbar
    # ahb2apb_pt has three units: three jobs, each reading only its own harness.
    out = generate(load(P2 / "ahb2apb_pt.ir.yaml"), tmp_path / "pt", P2)
    assert [f for f in out.formal if "_route" in f] == [
        "formal/demo_ss_route_ahb_bus.sby",
        "formal/demo_ss_route_aon_apb.sby",
        "formal/demo_ss_route_br_ahb2aon.sby",
    ]
    bridge = (tmp_path / "pt" / "formal" / "demo_ss_route_br_ahb2aon.sv").read_text(
        encoding="utf-8"
    )
    assert "module bag_demo_ss_unit_br_ahb2aon (" in bridge
    assert "module bag_demo_ss_unit_ahb_bus (" not in bridge
    # The bridge's window: the downstream address within the upstream word, both windows 0
    # (one domain: the request being presented counts too).
    assert ("br_ahb2aon_p_in_window: assert ((bag_busy || bag_up_req) && "
            "((br_ahb2aon_2_aon_apb_apb4_paddr ^ 32'(65'(bag_win_addr) + "
            "65'(64'h0000_0000_0000_0000) - 65'(64'h0000_0000_0000_0000))) & ~32'(3)) == '0);"
            ) in bridge  # fmt: skip


def test_a_slave_port_request_must_match_its_masters_oldest_entry(tmp_path: Path) -> None:
    """D71: each master's accepted requests wait in a scoreboard as the reference has them
    (the address after the master's aliases, write, non-secure bit, the slave port), and a
    request at slave port k must equal its master's oldest entry for k, or the request the
    master presents now; the slave's handshake takes it. A miss or a denial (the reference's
    target is the slave count) is never entered. On an AXI4 crossbar the ID names the
    master; on an ID-less fabric with several masters the environment keeps two masters'
    identical requests to one slave from being open at once (D78), so one entry matches."""
    generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path / "xr", P3)
    xbar = (tmp_path / "xr" / "formal" / "axi_xbar_rules_route_xbar.sv").read_text(encoding="utf-8")
    assert "  wire [31:0] sb_aw0_a = xbar_aliased(0, 32'(dma_axi_awaddr));\n" in xbar
    assert "  wire sb_aw0_acc = (dma_axi_awvalid && dma_axi_awready);\n" in xbar
    assert ("  wire sb_aw0_p = dma_axi_awvalid && !sb_aw0_early && sb_aw0_t != 2'(3);  "
            "// presented now, bound for a slave\n") in xbar  # fmt: skip
    assert "      if (sb_aw0_acc && !sb_aw0_early && !took && sb_aw0_t != 2'(3)) begin\n" in xbar
    assert ("  wire sb_aw0_take0 = (regs_axi_awvalid && regs_axi_awready) && sb_aw0_m0;\n"
            ) in xbar  # fmt: skip
    assert "  logic [3:0][31:0] sb_aw0_addr = '0;\n" in xbar  # 4 entries for AXI4
    assert "clash" not in xbar
    generate(load(P2 / "ahb_fixed.ir.yaml"), tmp_path / "af", P2)
    ahb = (tmp_path / "af" / "formal" / "ahb_fixed_route_ahb.sv").read_text(encoding="utf-8")
    assert SCOREBOARD[Protocol.AHB_LITE] == 2
    assert "  logic [1:0][31:0] sb_a0_addr = '0;\n" in ahb
    assert "  always_comb if (sb_a1_ask) assume (!sb_a_clash_0_1);\n" in ahb
    assert "  always_comb if (sb_a0_ask) assume (!sb_a_clash_1_0);\n" in ahb
    # Two requests presented together are compared once; queued entries both ways.
    assert "    sb_a_clash_0_1 = sb_a0_ask && sb_a0_t == sb_a1_t && " in ahb
    assert "    sb_a_clash_1_0 = 1'b0;\n" in ahb
    # The assumption leaves two masters asking for one slave at once (formal review 3).
    assert ("  always @(posedge clk_h) cover_ahb_a_contended: cover (bag_live_D_H && sb_a0_ask && "
            "sb_a1_ask && sb_a0_t == sb_a1_t);\n") in ahb  # fmt: skip
    # The ID-less AXI4-Lite shared bus gets D78 too, on both channels (verification review 7).
    generate(load(P3 / "axil_shared_fixed.ir.yaml"), tmp_path / "asf", P3)
    lite = (tmp_path / "asf" / "formal" / "axil_shared_fixed_route_bus.sv").read_text(
        encoding="utf-8"
    )
    for ch in ("aw", "ar"):
        assert f"  always_comb if (sb_{ch}1_ask) assume (!sb_{ch}_clash_0_1);\n" in lite, ch
        assert f"cover_bus_{ch}_contended: cover (" in lite, ch


def test_no_scoreboard_assumption_constrains_the_fabric(tmp_path: Path) -> None:
    """RTL and formal review: on AHB-Lite a master's address phase is accepted by the fabric's
    HREADY, so the scoreboard's cap and D78 assumptions, written on the accepted phase, could
    be met by holding HREADY low: a fabric that took a third address phase was pruned, not
    failed. The assumptions now read only what the environment drives (HTRANS, VALID, the
    setup phase), and on AHB-Lite the cap is the fabric's, asserted (_buffered)."""
    generate(load(P2 / "ahb_fixed.ir.yaml"), tmp_path, P2)
    text = (tmp_path / "formal" / "ahb_fixed_route_ahb.sv").read_text(encoding="utf-8")
    sb = [line for line in text.splitlines() if "assume (" in line and "sb_" in line]
    assert sb and all("hready" not in line for line in sb), sb
    assert "  wire sb_a0_ask = (m0_ahb_htrans[1]) && sb_a0_t != 2'(3);  " in text
    assert ("  always @(posedge clk_h) if (bag_live_D_H && (m0_ahb_htrans[1] && m0_ahb_hready)) "
            "ahb_m0_a_buffered: assert (sb_a0_n != 2'(2));  // the fabric takes no more\n"
            ) in text  # fmt: skip
    assert "assume (!((m0_ahb_htrans[1]" not in text
    # A response never pays for the request taken in its own cycle (formal review 1).
    assert "ahb_m0_a_delivered: assert (sb_a0_due != '0);\n" in text
