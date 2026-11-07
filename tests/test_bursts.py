# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""hdl_bag.bursts: which paths bag check's WRAP warning (D49) covers, against the route model."""

from pathlib import Path

from hdl_bag.bursts import WRAP, refusing_converter, wrap_refusals
from hdl_bag.ir import Spec, load
from hdl_bag.ir.model import AccessRule, Adapter
from hdl_bag.tests_gen.route_model import route

XBAR_RULES = Path(__file__).parent / "configs" / "p3" / "axi_xbar_rules.ir.yaml"


def _rom_behind_a_converter_and_denied_to_dbg() -> Spec:
    """axi_xbar_rules, where dbg's default slave is rom, with a dw adapter in front of rom and
    Access_Rules dbg -> rom none. A miss still goes to the default slave: Access_Rules stop only
    a region hit."""
    spec = load(XBAR_RULES)
    (xbar,) = spec.fabrics
    rom_dw = Adapter(name="xbar_dw_2_rom", type="dw", port="rom", fabric="xbar", chain_index=0,
                     template="bag_dw", params={"from": 64, "to": 32}, placed_in="x",
                     rtl_name="u_xbar_dw_2_rom")  # fmt: skip
    rules = (*xbar.access_rules, AccessRule(master="dbg", slave="rom", access="none"))
    return spec.model_copy(update={
        "adapters": (*spec.adapters, rom_dw),
        "fabrics": (xbar.model_copy(update={"access_rules": rules}),),
    })  # fmt: skip


def test_a_denied_default_slave_is_still_reached_on_a_miss() -> None:
    spec = _rom_behind_a_converter_and_denied_to_dbg()
    miss = route(spec, "dbg", 0x00DE_0000, False, False, {})
    assert miss.slave == "rom"
    assert refusing_converter(spec, "dbg", miss.bridges, miss.slave, WRAP, 4) == "xbar_dw_2_rom"
    assert wrap_refusals(spec)[("dbg", "xbar_dw_2_rom")] == ["rom"]


def test_conversion_to_lite_or_apb_refuses_wrap_without_a_warning() -> None:
    """Owner: the warning covers width converters only. bridges_pulp's AXI4 cpu reaches an
    axi2axil and an axi2apb bridge at its own width: both refuse WRAP, neither warns."""
    spec = load(XBAR_RULES.parent / "bridges_pulp.ir.yaml")
    for bridge in ("br_cfg", "br_per"):
        assert refusing_converter(spec, "cpu", (bridge,), None, WRAP, 4) == bridge
    assert wrap_refusals(spec) == {}


def test_a_width_converter_warns() -> None:
    spec = load(XBAR_RULES.parent / "axi_adapters.ir.yaml")
    assert wrap_refusals(spec)[("cpu", "cpu_dw_2_xbar")] == ["ddr", "per", "pmu", "sram", "wide"]
