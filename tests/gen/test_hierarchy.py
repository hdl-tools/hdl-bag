# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Sub-wrapper placement and routing (no EDA tools)."""

import re
from pathlib import Path

import pytest

from hdl_bag.gen import generate, sources
from hdl_bag.gen.hierarchy import collisions, plan
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import load

TESTS = Path(__file__).parents[1]
CONFIGS = TESTS / "configs" / "p2"
NESTED = CONFIGS / "ahb2apb_nested.ir.yaml"
GOLDEN_IR = TESTS / "golden" / "demo_ss.ir.yaml"
PORT = re.compile(r"^\s+(input|output)\s+logic\s+(?:\[(\d+):0\]\s+)?(\w+),?$")

# ahb2apb_nested: top { br_ahb2aon, aon_wrapper { ahb_bus, per_wrapper { aon_apb } } }


def _crossings(w: str) -> dict[str, bool]:
    p = plan(build_netlist(load(NESTED)))
    return {b.bus.name: b.inside_master for b in p.boundaries[w]}


def test_nested_routing() -> None:
    # aon_wrapper holds ahb_bus: its master (mcu0, outside), its slave itcm0 (outside),
    # the link to the bridge at the top; the APB link and slaves pass through it.
    assert _crossings("aon_wrapper") == {
        "mcu0": False,  # the master end (mcu0) is outside
        "itcm0": True,  # ahb_bus, the master end of the itcm0 link, is inside
        "ahb_bus_2_br_ahb2aon": True,
        "br_ahb2aon_2_aon_apb": False,  # the bridge (master end) is at the top
        "rtc0": True,
        "wdt0": True,
    }
    assert _crossings("per_wrapper") == {"br_ahb2aon_2_aon_apb": False, "rtc0": True, "wdt0": True}
    assert _crossings("demo_ss_bus_top") == {}


def _ports(text: str) -> dict[str, tuple[str, int]]:
    head = text[text.index("module ") : text.index(");")]
    return {m[3]: (m[1], int(m[2]) + 1 if m[2] else 1)
            for m in map(PORT.match, head.splitlines()) if m}  # fmt: skip


def test_nested_modules(tmp_path: Path) -> None:
    out = generate(load(NESTED), tmp_path, CONFIGS)
    order = [p.as_posix() for p in sources(out)]
    assert order[-3:] == ["rtl/per_wrapper.sv", "rtl/aon_wrapper.sv", "rtl/demo_ss_bus_top.sv"]
    per = _ports((tmp_path / "rtl/per_wrapper.sv").read_text())
    aon = _ports((tmp_path / "rtl/aon_wrapper.sv").read_text())
    # Directions follow the bus roles; widths are the bus's (12-bit slave addresses).
    assert per["br_ahb2aon_2_aon_apb_apb4_psel"] == ("input", 1)
    assert per["br_ahb2aon_2_aon_apb_apb4_prdata"] == ("output", 32)
    assert per["wdt0_apb4_paddr"] == ("output", 12)
    assert aon["mcu0_ahb_haddr"] == ("input", 32) and aon["mcu0_ahb_hrdata"] == ("output", 32)
    assert aon["ahb_bus_2_br_ahb2aon_ahb_hsel"] == ("output", 1)
    # Tied signals are not routed (mcu0 LOCK, wdt0/rtc0 PSTRB).
    assert "mcu0_ahb_hmastlock" not in aon and "wdt0_apb4_pstrb" not in per
    # Clocks and conditioned resets go only where used: the APB mux has no clock.
    assert "clk_aon" not in per and "bag_rst_D_AON_n" not in per
    assert aon["clk_aon"] == ("input", 1) and aon["bag_rst_D_AON_n"] == ("input", 1)
    top = (tmp_path / "rtl/demo_ss_bus_top.sv").read_text()
    assert "aon_wrapper u_aon_wrapper (" in top
    assert ".br_ahb2aon_2_aon_apb_apb4_paddr(br_ahb2aon_2_aon_apb_paddr)," in top
    assert ".wdt0_apb4_paddr(wdt0_apb_paddr)," in top  # the top's port, by its prefix
    aon_text = (tmp_path / "rtl/aon_wrapper.sv").read_text()
    assert "per_wrapper u_per_wrapper (" in aon_text
    assert ".wdt0_apb4_paddr(wdt0_apb4_paddr)," in aon_text  # passed straight through
    # Placement keeps the RTL instance name from Hierarchy.
    assert "  ) u_apb_mux (" in (tmp_path / "rtl/per_wrapper.sv").read_text()


def test_golden_plan_has_no_collisions() -> None:
    net = build_netlist(load(GOLDEN_IR))
    p = plan(net)
    assert p.wrappers() == ["aon_wrapper", "per_wrapper", "main_wrapper", "demo_ss_bus_top"]
    assert collisions(net, p) == []
    per = {b.bus.name for b in p.boundaries["per_wrapper"]}
    assert per == {"br_main2per_2_periph_apb", "gpio0", "spi0", "timer0", "uart0"}


def test_boundary_collision_stops_generation(tmp_path: Path) -> None:
    spec = load(NESTED)
    w = spec.wrapper.model_copy(update={"extra_inputs": ("boot_remap_i", "wdt0_apb4_paddr")})
    with pytest.raises(
        GenerateError, match="wdt0_apb4_paddr of sub-wrapper aon_wrapper is claimed by extra"
    ):
        generate(spec.model_copy(update={"wrapper": w}), tmp_path, CONFIGS)


def test_clock_named_like_a_boundary_port_is_refused(tmp_path: Path) -> None:
    spec = load(NESTED)
    clock = spec.clocks[0].model_copy(update={"name": "wdt0_apb4_paddr"})
    with pytest.raises(GenerateError, match="is claimed by clock wdt0_apb4_paddr and link wdt0"):
        generate(spec.model_copy(update={"clocks": (clock,)}), tmp_path, CONFIGS)
