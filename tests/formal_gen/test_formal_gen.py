# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation without EDA tools: what the quiet-in-reset properties watch, their
environment, the SymbiYosys job, and that every port they bind to exists on the top."""

import re
from pathlib import Path

import pytest

from hdl_bag.formal_gen import COVER_DEPTH, PROTOCOL_DEPTH, PROVE_DEPTH, checkers, props_module
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.ir import load
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


def test_a_sync_assertion_gets_the_synchronizer_stages_first(tmp_path: Path) -> None:
    """ahb_fixed's reset asserts synchronously: bag_rst_sync passes it on after 2 edges, so
    the domain is checked from then on (the generated reset test allows the same), and the
    reset may only be released once it has taken effect (D51)."""
    text = _props(tmp_path, P2 / "ahb_fixed.ir.yaml")
    assert "  wire bag_quiet_D_H = bag_held_D_H && bag_edges_D_H == 2'd2;\n" in text
    release = "always_comb if (bag_was_rst_h_n && !bag_held_D_H) assume (bag_quiet_was_D_H);"
    assert f"  {release}\n" in text
    assert re.search(r"if \(bag_quiet_D_H\) quiet_\w+: assert", text)
    assert not re.search(r"if \(bag_held_D_H\) quiet_\w+: assert", text)


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
    # And every checked protocol port has a cover of its own.
    spec = load(config)
    protocol = tmp_path / "formal" / f"{spec.project.name}_protocol.sv"
    if protocol.is_file():
        ports = set(re.findall(r"proto_\w+?_(\w+): cover", protocol.read_text(encoding="utf-8")))
        found = {c.port for c in checkers(spec, build_netlist(spec))}
        assert found and all(any(p.endswith(port) for p in ports) for port in found), found
    # Step registers come from formal/bag_step.v: under yosys-slang a register on a net
    # marked (* gclk *) is clocked by a free net, not the solver's step (PR 1's bug).
    assert "gclk" not in text and "always_ff @(posedge bag_step)" not in text


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
    assert out.formal == ("formal/demo_ss_protocol.sby", "formal/demo_ss_reset.sby")
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


def test_an_axi4_port_gets_the_handshake_rule_on_what_the_environment_drives(
    tmp_path: Path,
) -> None:
    """Until PR 2b brings BAG's AXI4 properties, the environment still follows the AXI
    handshake rule: a master that withdrew a stalled request would move a fabric's
    arbitration under a stalled transfer downstream (found on mixed_lite_shared)."""
    text = _protocol(tmp_path, GOLDEN)
    for line in [
        "  always_ff @(posedge clk_cpu) bag_pend_cpu0_aw <= cpu0_axi_awvalid && !cpu0_axi_awready;",
        "  always_comb if (bag_pend_cpu0_aw && !bag_checker_reset_D_CPU) "
        "assume (cpu0_axi_awvalid);",
        "  always_ff @(posedge clk_sys) bag_pend_ddr0_r <= ddr0_axi_rvalid && !ddr0_axi_rready;",
    ]:
        assert f"{line}\n" in text, line
    assert "bag_pend_cpu0_b" not in text  # the design drives B to a master: PR 2b asserts it


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
    assert out.formal == ("formal/apb3_wide_protocol.sby", "formal/apb3_wide_reset.sby")
    job = (tmp_path / out.formal[0]).read_text(encoding="utf-8")
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


def test_no_protocol_job_without_an_axi4_lite_or_apb_port(tmp_path: Path) -> None:
    out = generate(load(P3 / "axi_xbar_rules.ir.yaml"), tmp_path, P3)
    assert out.formal == ("formal/axi_xbar_rules_reset.sby",)
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


def test_the_protocol_covers_run_at_the_bmc_depth(tmp_path: Path) -> None:
    """So each cover reached is a state the bounded check explored."""
    generate(load(P2 / "apb4_rules.ir.yaml"), tmp_path, P2)
    job = (tmp_path / "formal" / "apb4_rules_protocol.sby").read_text(encoding="utf-8")
    assert f"bmc: depth {PROTOCOL_DEPTH}\ncover: mode cover\ncover: depth {PROTOCOL_DEPTH}\n" in job
