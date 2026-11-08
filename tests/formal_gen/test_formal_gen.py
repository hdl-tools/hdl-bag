# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation without EDA tools: what the quiet-in-reset properties watch, their
environment, the SymbiYosys job, and that every port they bind to exists on the top."""

import re
from pathlib import Path

import pytest

from hdl_bag.formal_gen import COVER_DEPTH, PROVE_DEPTH, props_module
from hdl_bag.gen import generate, include_dirs, sources
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
    for line in [
        f"{cpu} quiet_cpu0_axi_bvalid: assert (cpu0_axi_bvalid == 1'd0);",
        f"{cpu} quiet_cpu0_axi_rvalid: assert (cpu0_axi_rvalid == 1'd0);",
        "always_comb if (bag_held_D_CPU) assume (cpu0_axi_awvalid == 1'd0);",
        f"{sys} quiet_ddr0_axi_awvalid: assert (ddr0_axi_awvalid == 1'd0);",
        "always_comb if (bag_held_D_SYS) assume (ddr0_axi_bvalid == 1'd0);",
        f"{aon} quiet_mcu0_ahb_hready: assert (mcu0_ahb_hready == 1'd1);",
        "always_comb if (bag_held_D_AON) assume (mcu0_ahb_htrans == 2'd0);",
        f"{aon} quiet_itcm0_ahb_htrans: assert (itcm0_ahb_htrans == 2'd0);",
        "always_comb if (bag_held_D_AON) assume (itcm0_ahb_hreadyout == 1'd1);",
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
        f"always_ff @(posedge bag_step) bag_busy_cpu0_axi_bvalid <= {done} && !bag_held_D_CPU "
        "&& cpu0_axi_bvalid != 1'd0;",
        "always_ff @(posedge bag_step) if (bag_held_D_CPU && bag_busy_cpu0_axi_bvalid) "
        "bag_hit_cpu0_axi_bvalid <= 1'b1;",
        "always_comb reset_mid_cpu0_axi_bvalid: cover "
        "(bag_quiet_D_CPU && bag_hit_cpu0_axi_bvalid);",
        "always_ff @(posedge bag_step) if (bag_quiet_D_CPU) bag_done_D_CPU <= 1'b1;",
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


@pytest.mark.parametrize("config", [c for c in CONFIGS if not _opaque(c)], ids=lambda c: c.stem)
def test_every_port_the_properties_bind_to_is_a_port_of_the_top(tmp_path: Path,
                                                                config: Path) -> None:  # fmt: skip
    """``bind ... (.*)`` connects by name: each input of the property module must be a port
    of the lint top, whatever the signal pattern and prefixes."""
    spec = load(config)
    for style in ("flat", "sv-interface"):
        out = generate(spec, tmp_path / style, config.parent, style)
        top = (tmp_path / style / "rtl" / f"{out.top}.sv").read_text(encoding="utf-8")
        props = (tmp_path / style / "formal" / f"{spec.project.name}_reset.sv").read_text(
            encoding="utf-8"
        )
        module = props.split(f"module {props_module(spec)} (", 1)[1].split(");", 1)[0]
        wanted = set(_PORT.findall(module))
        top_ports = set(_PORT.findall(top.split(");", 1)[0]))
        assert wanted and wanted <= top_ports, sorted(wanted - top_ports)
        assert props.endswith(f"bind {out.top} {props_module(spec)} u_{props_module(spec)} (.*);\n")


def test_the_job_reads_the_file_list_then_the_properties(tmp_path: Path) -> None:
    spec = load(GOLDEN)
    out = generate(spec, tmp_path, GOLDEN.parent)
    assert out.formal == ("formal/demo_ss_reset.sby",)
    job = (tmp_path / out.formal[0]).read_text(encoding="utf-8")
    read = " ".join([*(f"-I {d.as_posix()}" for d in include_dirs(out)), "--top", out.top,
                     *(f.as_posix() for f in sources(out)), "demo_ss_reset.sv"])  # fmt: skip
    assert f"\nread_slang {read}\nprep -top {out.top}\n" in job
    assert f"prove: depth {PROVE_DEPTH}\n" in job and f"cover: depth {COVER_DEPTH}\n" in job
    assert job.endswith("[files]\nrtl ../rtl\ndemo_ss_reset.sv\n")
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
