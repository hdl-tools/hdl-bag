# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generic generator support for AXI (Phase 3 PR 2): bus ID/user widths through adapters
and bridges, width fitting in the instance helpers, and the AXI package parameters."""

import re
from functools import cache
from importlib.resources import files
from pathlib import Path

import pytest
from dsl import AHB_MASTER_NA, AXI4_NA, AXIL_NA, Cfg, resolve_cfg

from hdl_bag.gen.netlist import Bus, Instance, build_netlist
from hdl_bag.gen.package import LATENCY_MODE, package
from hdl_bag.gen.render import lit
from hdl_bag.gen.wrapper import InstanceContext, Scope
from hdl_bag.ir.model import Spec
from hdl_bag.templates import builtin_registry
from hdl_bag.templates.sources import VENDOR


def axi_cfg() -> Cfg:
    """AXI4 crossbar with every adapter kind the netlist derives widths for."""
    c = Cfg("axi_gen", phase=3)
    c.domain("D_SYS", "clk_sys", "rst_sys_n").domain("D_CPU", "clk_cpu", "rst_cpu_n")
    c.fabric("xbar", "AXI4", 64, 32, "D_SYS", "round-robin", latency="CUT_ALL_AX",
             max_trans=(8, 8))  # fmt: skip
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_SYS")
    c.master("cpu", "AXI4", 128, 32, "D_CPU", id_width=4)  # cdc, dw
    c.master("dma", "AXI4", 64, 24, "D_SYS", id_width=2, user=3)  # narrow address
    c.master("dbg", "AXI4-Lite", 32, 32, "D_SYS")  # pc, dw
    c.master("mcu", "AHB-Lite", 32, 32, "D_SYS")
    c.slave("mem", "AXI4", 64, 32, "D_SYS", id_width=3, user=3)  # iw
    c.slave("ram", "AXI4", 64, 32, "D_SYS")  # ID width auto
    c.bridge("br", 0x1000_0000, template="bag_ahb2axi")
    for i, m in enumerate(("cpu", "dma", "dbg", "br")):
        c.link(m, "xbar", i)
    c.link("mcu", "ahb", 0).link("ahb", "br", 0)
    c.link("xbar", "mem", 0).link("xbar", "ram", 1)
    c.region("mem", "mem", 0x8000_0000, 0x1000_0000)
    c.region("ram", "ram", 0x1000_0000, 0x10_0000)
    c.region("br", "win", 0x1000_0000, 0x10_0000)
    for m in ("cpu", "dma", "dbg", "br"):
        c.decode(m, "DECERR")
    c.decode("mcu", "HRESP_ERROR")
    for p in ("cpu", "dma", "mem", "ram", "br"):
        c.option(p, **AXI4_NA)
    c.option("dbg", **AXIL_NA).option("mcu", **AHB_MASTER_NA)
    return c.wrap_refused("cpu", "cpu_dw_2_xbar", "mem", "ram")


@cache
def _spec(tmp: str) -> Spec:
    spec, findings = resolve_cfg(axi_cfg(), Path(tmp))
    assert spec is not None, "\n".join(findings)
    return spec


@pytest.fixture(scope="module")
def spec(tmp_path_factory: pytest.TempPathFactory) -> Spec:
    return _spec(str(tmp_path_factory.mktemp("axi_gen")))


def _inst(spec: Spec, name: str) -> Instance:
    return next(i for i in build_netlist(spec).instances if i.name == name)


def test_config_resolves_with_the_expected_adapters(spec: Spec) -> None:
    kinds = sorted((a.port, a.type) for a in spec.adapters)
    assert kinds == [("cpu", "cdc"), ("cpu", "dw"), ("dbg", "dw"), ("dbg", "pc"), ("mem", "iw")]
    xbar = next(f for f in spec.fabrics if f.name == "xbar")
    assert xbar.id_width is not None
    assert (xbar.id_width.master_side, xbar.id_width.slave_side) == (4, 6)


def test_iw_adapter_takes_the_slave_to_the_fabric_id(spec: Spec) -> None:
    iw = next(i for i in build_netlist(spec).instances if i.adapter and i.adapter.type == "iw")
    assert iw.up is not None and iw.down is not None
    # Slave side: up is towards the fabric (its slave-side ID), down is the slave port.
    assert (iw.up.id_width, iw.down.id_width) == (6, 3)
    assert iw.up.user_width == iw.down.user_width == 3
    assert "awid" in iw.up.signals and "awuser" in iw.up.signals


def test_bridge_drives_its_manifest_id_width(spec: Spec) -> None:
    br = _inst(spec, "br")
    assert br.down is not None and br.down.id_width == 1 and "arid" in br.down.signals


def test_pc_to_axi4_has_no_id(spec: Spec) -> None:
    pc = next(i for i in build_netlist(spec).instances if i.adapter and i.adapter.type == "pc")
    assert pc.down is not None and pc.down.protocol == "AXI4"
    assert pc.down.id_width == 0 and "awid" not in pc.down.signals


def test_bridges_into_axi4_match_the_checks_assumption() -> None:
    """checks/context.py derives fabric ID widths assuming a bridge master issues ID 1."""
    reg = builtin_registry()
    for name in reg.names("bridge"):
        m = reg.get(name)
        assert m is not None
        assert m.down_id_width == (1 if "AXI4" in m.downstream else None), name


def _ctx(spec: Spec, name: str) -> tuple[InstanceContext, Scope]:
    scope = Scope(spec, "flat", {})
    return InstanceContext(scope, _inst(spec, name)), scope


def test_vec_uses_fabric_id_and_user_widths(spec: Spec) -> None:
    ctx, scope = _ctx(spec, "xbar")
    inst = ctx.inst
    ctx.vec("s_awid", inst.masters, "awid", "in")
    ctx.vec("m_awid", inst.slaves, "awid", "out")
    ctx.vec("s_awuser", inst.masters, "awuser", "in")
    decls = "\n".join(scope.decls)
    assert "logic [3:0][3:0] u_xbar_s_awid;" in decls  # 4 masters, master-side ID 4
    assert "logic [1:0][5:0] u_xbar_m_awid;" in decls  # 2 slaves, slave-side ID 6
    assert "logic [3:0][2:0] u_xbar_s_awuser;" in decls  # widest user signal: 3
    assigns = "\n".join(scope.assigns)
    assert f"assign u_xbar_s_awid[1] = {{{lit(2, 0)}, dma_awid}};" in assigns  # ID 2, extended
    assert f"assign u_xbar_s_awid[2] = {lit(4, 0)};" in assigns  # dbg (via pc): no ID
    assert f"assign u_xbar_s_awid[3] = {{{lit(3, 0)}, br_2_xbar_awid}};" in assigns  # bridge: 1


def test_input_fitting(spec: Spec) -> None:
    ctx, _ = _ctx(spec, "xbar")
    dma = next(b for b in ctx.inst.masters if b.name == "dma")
    assert dma.addr_width == 24
    assert ctx.i(dma, "awaddr", width=32) == f"{{{lit(8, 0)}, dma_awaddr}}"
    assert ctx.i(dma, "awaddr", width=16) == "dma_awaddr[15:0]"
    assert ctx.i(dma, "awaddr") == "dma_awaddr"


def test_output_fitting(spec: Spec) -> None:
    ctx, scope = _ctx(spec, "xbar")
    dma = next(b for b in ctx.inst.masters if b.name == "dma")
    net = ctx.o(dma, "rid", width=6)  # component drives 6 bits into a 2-bit bus signal
    assert net == "u_xbar_dma_rid_fit"
    assert "  assign dma_rid = u_xbar_dma_rid_fit[1:0];" in scope.assigns
    assert "  assign unused_u_xbar_dma_rid_fit_msb = u_xbar_dma_rid_fit[5:2];" in scope.assigns
    wide = Bus("w", dma.protocol, 32, 64, 8, 0, "D_SYS", False, ("rid",))
    assert ctx.o(wide, "rid", width=4) == "u_xbar_w_rid_fit"
    assert f"  assign w_rid = {{{lit(4, 0)}, u_xbar_w_rid_fit}};" in scope.assigns


def test_package_axi_parameters(spec: Spec) -> None:
    text = package(spec, "// header\n")
    for line in (
        "localparam int unsigned XbarDataWidth = 64;",
        "localparam int unsigned XbarIdWidthMst = 4;",
        "localparam int unsigned XbarIdWidthSlv = 6;",
        "localparam int unsigned XbarUserWidth = 3;",
        "localparam int unsigned XbarMaxMstTrans = 8;",
        "localparam logic [9:0] XbarLatencyMode = 10'h252;",
    ):
        assert line in text
    assert "AhbIdWidth" not in text and "AhbDataWidth" not in text  # AHB: no AXI section


def test_latency_modes_match_vendored_axi_pkg() -> None:
    text = (files("hdl_bag") / VENDOR / "axi/src/axi_pkg.sv").read_text(encoding="utf-8")
    bits = {
        m[1]: 1 << int(m[2]) for m in re.finditer(r"bit \[9:0\] (\w+)\s*= \(1 << (\d+)\)", text)
    }
    enum = re.search(r"typedef enum bit \[9:0\] \{(.*?)\} xbar_latency_e;", text, re.S)
    assert enum and len(bits) == 10
    for name, expr in re.findall(r"(\w+)\s*=\s*([^,\n]+)", enum[1]):
        value = expr.strip()
        if value.startswith("10'b"):
            expected = int(value[4:].replace("_", ""), 2)
        else:
            expected = 0
            for term in value.split("|"):
                expected |= bits[term.strip()]
        assert LATENCY_MODE[name] == expected, name
    assert set(LATENCY_MODE) == set(re.findall(r"(\w+)\s*=", enum[1]))


def test_axi_interfaces(spec: Spec) -> None:
    from hdl_bag.gen.wrapper import render_interface
    from hdl_bag.protocols import Protocol

    lite = render_interface(spec, Protocol.AXI4_LITE, "// h\n")
    full = render_interface(spec, Protocol.AXI4, "// h\n")
    assert "interface axi_gen_axil_if" in lite
    assert "IdWidth" not in lite and "UserWidth" not in lite and "awid" not in lite
    assert "interface axi_gen_axi_if" in full
    assert "IdWidth" in full and "UserWidth" in full and "awid" in full and "awuser" in full


def test_miss_response_table_by_port_index() -> None:
    """MissResp follows the Port index: dma (0) SLVERR, cpu (1) DECERR, dbg (2, default
    slave) the unused DECERR filler."""
    from hdl_bag.ir import load

    root = Path(__file__).parents[1] / "configs" / "p3"
    text = package(load(root / "axi_xbar_rules.ir.yaml"), "// header\n")
    assert "localparam logic [2:0][1:0] XbarMissResp = '{2'b11, 2'b11, 2'b10};" in text


def test_slave_side_pc_carries_the_fabric_slave_id() -> None:
    """An AXI4-Lite slave behind pc: the AXI4 buses on the fabric side of the pc carry the
    fabric's slave-side ID (the pc reflects IDs), so B/R return to the right master."""
    from hdl_bag.ir import load

    spec = load(Path(__file__).parents[1] / "configs" / "p3" / "axi_adapters.ir.yaml")
    xbar = next(f for f in spec.fabrics if f.name == "xbar")
    assert xbar.id_width is not None
    insts = {i.name: i for i in build_netlist(spec).instances}
    pc, dw = insts["xbar_pc_2_pmu"], insts["xbar_dw_2_pmu"]
    assert pc.up is not None and pc.down is not None and dw.up is not None
    assert pc.down.protocol == "AXI4-Lite" and pc.down.id_width == 0
    assert pc.up.id_width == xbar.id_width.slave_side and "bid" in pc.up.signals
    assert dw.up.id_width == xbar.id_width.slave_side  # the fabric slot


def test_width_conversion_on_the_axi4_side_of_pc() -> None:
    """D33: an AXI4 port on an AXI4-Lite fabric converts width while still AXI4."""
    from hdl_bag.ir import load

    spec = load(Path(__file__).parents[1] / "configs" / "p3" / "axil_adapters.ir.yaml")

    def chain(port: str) -> list[str]:
        own = sorted((a for a in spec.adapters if a.port == port), key=lambda a: a.chain_index)
        return [a.type for a in own]

    assert chain("cpu") == ["dw", "pc"]  # master: port -> dw (AXI4) -> pc -> fabric
    assert chain("mem") == ["dw", "pc"]  # slave, from the port: port <- dw (AXI4) <- pc <- fabric
    insts = {i.name: i for i in build_netlist(spec).instances}
    dw = insts["cpu_dw_2_cfg"]
    assert dw.up is not None and dw.down is not None
    assert (dw.up.protocol, dw.down.protocol) == ("AXI4", "AXI4")
    assert (dw.up.data_width, dw.down.data_width) == (64, 32)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("bridges_pulp", {
            "u_br_per": ("bag_axi2apb", 32, 0x4000_0000, 2),
            "u_br_cfg": ("bag_axi2axil", 32, 0, 2),
            "u_br_lp": ("bag_axil2apb", 32, 0, 0),
        }),
        # br_per and br_led sit behind br_aon's pass-through N window (0x4000_0000): br_per
        # (N) subtracts only the rest of its window, br_led (Y) nothing.
        ("bridges_pulp_cdc", {
            "u_br_aon": ("bag_axi2axil", 32, 0x4000_0000, 0),
            "u_br_per": ("bag_axil2apb", 24, 0x0080_0000, 2),
            "u_br_led": ("bag_axil2apb", 24, 0, 0),
            "u_br_sys": ("bag_axi2apb", 32, 0x4200_0000, 1),
        }),
    ],
)  # fmt: skip
def test_bridge_instances(
    tmp_path: Path, config: str, expected: dict[str, tuple[str, int, int, int]]
) -> None:
    """Each bridge instantiates the module for its upstream protocol, with the offset the
    chain of pass-through windows gives it and the IR's FIFO depth."""
    from hdl_bag.gen import generate
    from hdl_bag.ir import load

    root = Path(__file__).parents[1] / "configs" / "p3"
    spec = load(root / f"{config}.ir.yaml")
    out = generate(spec, tmp_path, root, "flat")
    top = (tmp_path / f"rtl/{spec.hierarchy.top}.sv").read_text()
    insts = {
        m[3]: (m[1], m[2]) for m in re.finditer(r"\n  (\w+) #\((.*?)\n  \) (u_\w+) \(", top, re.S)
    }
    assert out.top
    for name, (module, aw, offset, depth) in expected.items():
        mod, params = insts[name]
        assert mod == module, name
        assert f"({lit(aw, offset)})" in params.split(".AddrOffset", 1)[1].split("\n", 1)[0], name
        assert re.search(rf"\.FifoDepth\s*\({depth}\)", params), name
