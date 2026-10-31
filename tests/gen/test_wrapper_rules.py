# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generator rules without EDA tools: port list, ties, resets, tables, guards."""

import os
import re
import subprocess
import sys
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from hdl_bag.gen import MissingRtl, generate
from hdl_bag.gen.decode import decode_tables, fabric_offset
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.render import GenerateError, lit
from hdl_bag.ir import Spec, dump, load
from hdl_bag.ir.model import Alias, Decode, PortRef

TESTS = Path(__file__).parents[1]
CONFIG = TESTS / "configs" / "p2" / "ahb2apb_pt.ir.yaml"
GOLDEN_IR = TESTS / "golden" / "demo_ss.ir.yaml"
PORT = re.compile(r"^\s+(input|output)\s+logic\s+(?:\[(\d+):0\]\s+)?(\w+),?$")


def _spec() -> Spec:
    return load(CONFIG)


def _with_option(spec: Spec, port: str, **fields: str) -> Spec:
    opts = tuple(o.model_copy(update=fields) if o.port == port else o for o in spec.port_options)
    return spec.model_copy(update={"port_options": opts})


def _gen(spec: Spec, root: Path, style: str = "flat") -> dict[str, str]:
    generate(spec, root, CONFIG.parent, style)
    return {p.name: p.read_text() for p in (root / "rtl").glob("*.sv")}


def _ports(text: str) -> dict[str, tuple[str, int]]:
    header = text[text.index("module ") : text.index(");")]
    out = {}
    for line in header.splitlines():
        m = PORT.match(line)
        if m:
            out[m[3]] = (m[1], int(m[2]) + 1 if m[2] else 1)
    return out


# ---------------------------------------------------------------- port list

# Written out by hand from the IR (not from the generator): the flat wrapper's ports.
EXPECTED_PORTS = {
    "clk_aon": ("input", 1), "rst_aon_n": ("input", 1), "boot_remap_i": ("input", 1),
    **{f"mcu0_ahb_{s}": ("input", w) for s, w in (
        ("haddr", 32), ("hburst", 3), ("hprot", 4), ("hnonsec", 1), ("hsize", 3),
        ("htrans", 2), ("hwdata", 32), ("hwrite", 1))},  # hmastlock: LOCK tied
    **{f"mcu0_ahb_{s}": ("output", w) for s, w in (("hrdata", 32), ("hready", 1), ("hresp", 1))},
    **{f"itcm0_ahb_{s}": ("output", w) for s, w in (
        ("hsel", 1), ("haddr", 16), ("hburst", 3), ("hprot", 4), ("hnonsec", 1), ("hsize", 3),
        ("htrans", 2), ("hwdata", 32), ("hwrite", 1), ("hready", 1))},
    **{f"itcm0_ahb_{s}": ("input", w) for s, w in (("hrdata", 32), ("hreadyout", 1), ("hresp", 1))},
    **{f"{p}_apb_{s}": ("output", w) for p in ("wdt0", "rtc0") for s, w in (
        ("paddr", 12), ("pprot", 3), ("psel", 1), ("penable", 1), ("pwrite", 1), ("pwdata", 32))},
    **{f"{p}_apb_{s}": ("input", w) for p in ("wdt0", "rtc0") for s, w in (
        ("pready", 1), ("prdata", 32), ("pslverr", 1))},  # pstrb: PSTRB tied
}  # fmt: skip


def test_flat_port_list_matches_the_ir(tmp_path: Path) -> None:
    flat = _ports(_gen(_spec(), tmp_path)["demo_ss_bus_top.sv"])
    assert flat == EXPECTED_PORTS


def test_sv_interface_harness_has_the_flat_ports(tmp_path: Path) -> None:
    files = _gen(_spec(), tmp_path, "sv-interface")
    assert _ports(files["demo_ss_bus_top_flat.sv"]) == EXPECTED_PORTS
    top = files["demo_ss_bus_top.sv"]
    assert "demo_ss_ahb_if.to_master mcu0_ahb," in top
    assert "demo_ss_apb4_if.to_slave wdt0_apb" in top


# ---------------------------------------------------------------- ties


TIES = [("tie-0", "4'b0000", "1'b1"), ("tie-default", "4'b0011", "1'b1")]


@pytest.mark.parametrize(("mode", "hprot", "hnonsec"), TIES)
def test_master_prot_tie_fails_closed(tmp_path: Path, mode: str, hprot: str, hnonsec: str) -> None:
    """D28: a tied HNONSEC is non-secure; HPROT takes its tie value."""
    top = _gen(_with_option(_spec(), "mcu0", prot=mode), tmp_path)["demo_ss_bus_top.sv"]
    assert f"assign u_ahb_bus_s_hprot[0] = {hprot};" in top
    assert f"assign u_ahb_bus_s_hnonsec[0] = {hnonsec};" in top
    assert "mcu0_ahb_hprot" not in _ports(top) and "mcu0_ahb_hnonsec" not in _ports(top)


def test_slave_ties_in_both_styles(tmp_path: Path) -> None:
    spec = _with_option(_with_option(_spec(), "itcm0", prot="tie-default"), "rtc0", pslverr="tie-0")
    flat = _gen(spec, tmp_path / "f")["demo_ss_bus_top.sv"]
    assert "itcm0_ahb_hprot" not in _ports(flat) and "rtc0_apb_pslverr" not in _ports(flat)
    assert "assign u_aon_apb_m_pslverr[1] = 1'b0;" in flat  # rtc0 is slave port 1
    svif = _gen(spec, tmp_path / "s", "sv-interface")["demo_ss_bus_top.sv"]
    assert "assign itcm0_ahb.hprot = 4'b0011;" in svif  # wrapper-driven constants
    assert "assign itcm0_ahb.hnonsec = 1'b1;" in svif
    # PSTRB tie-default is all ones on writes and zero on reads (APB4).
    assert "assign rtc0_apb.pstrb = {4{u_aon_apb_m_pwrite[1]}};" in svif or (
        "assign rtc0_apb.pstrb = {4{rtc0_apb.pwrite}};" in svif
    )


def test_bridge_options_apply_to_its_downstream_end_only() -> None:
    spec = _with_option(_spec(), "br_ahb2aon", prot="tie-default", pslverr="tie-0")
    bridge = next(i for i in build_netlist(spec).instances if i.kind == "bridge")
    assert bridge.up is not None and bridge.down is not None
    assert bridge.up.ties == {}
    assert bridge.down.ties == {"pprot": 0b010, "pslverr": 0}


def test_golden_netlist() -> None:
    spec = load(GOLDEN_IR)
    net = build_netlist(spec)
    ports = {b.name: b for b in net.ports}
    # dma0 Port_Options: PROT tie-default (D28: AxPROT[1] = 1), REGION tie-0,
    # CACHE tie-default (0b0011), LOCK tie-0; QOS driven.
    assert ports["dma0"].ties == {
        "awprot": 0b010, "arprot": 0b010, "awregion": 0, "arregion": 0,
        "awcache": 0b0011, "arcache": 0b0011, "awlock": 0, "arlock": 0,
    }  # fmt: skip
    for inst in net.instances:
        if inst.fabric is not None:  # slots follow Port index, whatever the names
            assert [b.name for b in inst.masters] == [
                *_chain_ends(spec, inst.fabric.name, [p.port for p in sorted(
                    inst.fabric.master_ports, key=lambda p: p.index)])]  # fmt: skip
    # Adapter chains run port -> ... -> fabric in D9 order, CDC moving the domain.
    cpu0 = [i.adapter for i in net.instances if i.adapter and i.adapter.port == "cpu0"]
    order = [a.type for a in sorted((a for a in cpu0 if a), key=lambda a: a.chain_index)]
    d9 = ["cdc", "pc", "dw", "iw", "fifo", "cut"]
    assert order == [t for t in d9 if t in order]
    cdc = next(i for i in net.instances if i.adapter and i.adapter.type == "cdc")
    assert cdc.up is not None and cdc.down is not None and cdc.up.domain != cdc.down.domain


def _chain_ends(spec: Spec, fabric: str, ports: list[str]) -> list[str]:
    """The bus name at FABRIC's slot for each port: the last adapter, else the port."""
    out = []
    for port in ports:
        chain = sorted((a for a in spec.adapters if a.port == port and a.fabric == fabric),
                       key=lambda a: a.chain_index)  # fmt: skip
        bridge = next((b for b in spec.bridges if b.name == port), None)
        default = port if bridge is None else f"{port}_2_{bridge.downstream}"
        out.append(chain[-1].name if chain else default)
    return out


# ---------------------------------------------------------------- resets

RESETS = list(product(["active-low", "active-high"], ["async", "sync"], ["async", "sync"]))


@pytest.mark.parametrize(("polarity", "assertion", "deassertion"), RESETS)
def test_reset_conditioning(
    tmp_path: Path, polarity: str, assertion: str, deassertion: str
) -> None:
    spec = _spec()
    r = spec.resets[0].model_copy(update={"polarity": polarity, "assertion": assertion,
                                          "deassertion": deassertion})  # fmt: skip
    top = _gen(spec.model_copy(update={"resets": (r,)}), tmp_path)["demo_ss_bus_top.sv"]
    low = polarity == "active-low"
    if deassertion == "sync":
        assert f".ActiveLow  (1'b{int(low)})," in top
        assert f".AsyncAssert(1'b{int(assertion == 'async')})" in top
        assert ".rst_i (rst_aon_n)," in top and ".rst_no(bag_rst_D_AON_n)" in top
    else:
        assert "bag_rst_sync" not in top
        assert f"assign bag_rst_D_AON_n = {'' if low else '~'}rst_aon_n;" in top
    assert ".rst_ni       (bag_rst_D_AON_n)," in top  # the fabric uses the conditioned reset


def test_unused_domain_is_sunk(tmp_path: Path) -> None:
    golden = load(GOLDEN_IR)
    spec = _spec()
    clk = next(c for c in golden.clocks if c.domain == "D_SYS")
    rst = next(r for r in golden.resets if r.domain == "D_SYS")
    spec = spec.model_copy(update={"clocks": (*spec.clocks, clk), "resets": (*spec.resets, rst)})
    top = _gen(spec, tmp_path)["demo_ss_bus_top.sv"]
    assert "assign unused_clk_sys = clk_sys;" in top
    assert "assign unused_rst_sys_n = rst_sys_n;" in top


# ---------------------------------------------------------------- decode tables


def _fabric(spec: Spec, name: str) -> Any:
    return next(f for f in spec.fabrics if f.name == name)


def test_decode_tables_oracle() -> None:
    """Expected tables written from the golden IR rows, not from decode_tables()."""
    spec = load(GOLDEN_IR)
    xbar = _fabric(spec, "main_xbar")
    t = decode_tables(xbar)
    m = {p.port: p.index for p in xbar.master_ports}
    s = {p.port: p.index for p in xbar.slave_ports}
    for rule, ir in zip(t.rules, xbar.addr_rules, strict=True):
        assert (rule.base, rule.mask, rule.slave) == (ir.base, ir.end - ir.base, ir.slave_port)
        assert (rule.no_read, rule.no_write) == (ir.access == "WO", ir.access == "RO")
        assert rule.secure == ir.secure
    for a in xbar.access_rules:
        assert t.connect[m[a.master]][s[a.slave]] == (a.access != "none")
        assert t.mst_no_write[m[a.master]][s[a.slave]] == (a.access == "RO")
        assert t.mst_no_read[m[a.master]][s[a.slave]] == (a.access == "WO")
    cpu0 = next(d for d in xbar.decode if d.master == "cpu0").aliases[0]
    alias = t.aliases[0]
    assert (alias.master, alias.base, alias.mask, alias.to) == (
        m["cpu0"],
        cpu0.base,
        cpu0.size - 1,
        cpu0.to,
    )


def test_priority_and_default_slave() -> None:
    spec = _spec()
    ahb = _fabric(spec, "ahb_bus")
    two = ahb.model_copy(update={
        "arbitration": "fixed-priority",
        "master_ports": (PortRef(index=0, port="mcu0", priority=1),
                         PortRef(index=1, port="dbg", priority=0)),
        "decode": (Decode(master="mcu0", miss="default-slave", default_slave="br_ahb2aon",
                          aliases=()),
                   Decode(master="dbg", miss="HRESP_ERROR", default_slave=None,
                          aliases=(Alias(base=0x0, size=0x1000, to=0x5000_1000, enable="pin_i"),
                                   Alias(base=0x2000, size=0x100, to=0x0, enable="always")))),
    })  # fmt: skip
    t = decode_tables(two)
    assert t.priority == (1, 0)
    assert t.miss_to_default == (True, False) and t.default_slave == (1, 0)
    assert [(a.master, a.base, a.mask, a.to) for a in t.aliases] == [
        (1, 0x0, 0xFFF, 0x5000_1000),
        (1, 0x2000, 0xFF, 0x0),
    ]  # sorted by (master, base), whatever the input order
    assert set(t.alias_enables) == {"1", "pin_i"}


def test_region_access_polarity() -> None:
    apb = _fabric(_spec(), "aon_apb")
    ro_wo = (apb.addr_rules[0].model_copy(update={"access": "RO"}),
             apb.addr_rules[1].model_copy(update={"access": "WO"}))  # fmt: skip
    t = decode_tables(apb.model_copy(update={"addr_rules": ro_wo}))
    assert [(r.no_read, r.no_write) for r in t.rules] == [(False, True), (True, False)]


def test_pass_through_n_decodes_window_offsets() -> None:
    spec = _spec()
    br = spec.bridges[0].model_copy(update={"pass_through": False})
    apb = _fabric(spec, "aon_apb")
    alias = Alias(base=0x5000_8000, size=0x1000, to=0x5000_1000, enable="always")
    apb = apb.model_copy(
        update={"decode": (apb.decode[0].model_copy(update={"aliases": (alias,)}),)}
    )
    fabrics = tuple(apb if f.name == "aon_apb" else f for f in spec.fabrics)
    spec = spec.model_copy(update={"bridges": (br,), "fabrics": fabrics})
    assert fabric_offset(spec, apb) == 0x5000_0000
    t = decode_tables(apb, fabric_offset(spec, apb))
    assert [r.base for r in t.rules] == [0x0000_0000, 0x0000_1000]
    assert [(a.base, a.to) for a in t.aliases] == [(0x8000, 0x1000)]


def test_mixed_translation_is_rejected() -> None:
    spec = _spec()
    br = spec.bridges[0].model_copy(update={"pass_through": False})
    apb = _fabric(spec, "aon_apb")
    apb = apb.model_copy(update={"master_ports": (*apb.master_ports, PortRef(index=1, port="m"))})
    spec = spec.model_copy(update={"bridges": (br,)})
    with pytest.raises(GenerateError, match="different address translations"):
        fabric_offset(spec, apb)


# ---------------------------------------------------------------- guards and errors


def test_reset_name_collision_is_an_error(tmp_path: Path) -> None:
    spec = _spec()
    r = spec.resets[0].model_copy(update={"name": "bag_rst_D_AON_n"})
    c = spec.clocks[0].model_copy(update={"reset": "bag_rst_D_AON_n"})
    with pytest.raises(GenerateError, match="collides with a port"):
        generate(spec.model_copy(update={"resets": (r,), "clocks": (c,)}), tmp_path, CONFIG.parent)


def test_sub_wrappers_are_rejected_until_supported(tmp_path: Path) -> None:
    golden = load(GOLDEN_IR)
    spec = _spec()
    spec = spec.model_copy(update={"hierarchy": spec.hierarchy.model_copy(
        update={"wrappers": golden.hierarchy.wrappers[:1]})})  # fmt: skip
    with pytest.raises(GenerateError, match="sub-wrappers are not generated yet"):
        generate(spec, tmp_path, CONFIG.parent)


def test_package_name_collision_is_an_error(tmp_path: Path) -> None:
    spec = _spec()
    r = spec.memory_map[0]
    dup = r.model_copy(update={"slave": r.slave + "_", "base": r.base + 0x10_0000})
    spec = spec.model_copy(update={"memory_map": (*spec.memory_map, dup)})
    with pytest.raises(GenerateError, match="package names collide"):
        generate(spec, tmp_path, CONFIG.parent)


def test_lit_rejects_overflow() -> None:
    with pytest.raises(GenerateError):
        lit(8, 0x100)
    with pytest.raises(GenerateError):
        lit(0, 0)


def test_stale_outputs_are_removed(tmp_path: Path) -> None:
    generate(_spec(), tmp_path, CONFIG.parent, "sv-interface")
    assert (tmp_path / "rtl/demo_ss_bus_top_flat.sv").exists()
    generate(_spec(), tmp_path, CONFIG.parent, "flat")
    assert not (tmp_path / "rtl/demo_ss_bus_top_flat.sv").exists()
    assert not (tmp_path / "rtl/demo_ss_ahb_if.sv").exists()


# ---------------------------------------------------------------- determinism


def test_multi_tie_and_golden_byte_identical_across_processes(tmp_path: Path) -> None:
    spec = _with_option(_with_option(_spec(), "itcm0", prot="tie-default"), "mcu0", prot="tie-0")
    dump(spec, tmp_path / "multi.ir.yaml")
    (tmp_path / "header.txt").write_text((CONFIG.parent / "header.txt").read_text())
    trees = []
    for seed in ("0", "7", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        tree = {}
        for name, src in (("multi", tmp_path / "multi.ir.yaml"), ("golden", GOLDEN_IR)):
            out = tmp_path / seed / name
            cmd = [sys.executable, "-m", "hdl_bag.cli", "generate", str(src), "-o", str(out),
                   "--style", "sv-interface"]  # fmt: skip
            subprocess.run(cmd, env=env, capture_output=True, check=False)
            tree.update({f"{name}/{p.relative_to(out).as_posix()}": p.read_bytes()
                         for p in out.rglob("*") if p.is_file()})  # fmt: skip
        trees.append(tree)
    assert trees[0] == trees[1] == trees[2]
    assert "golden/rtl/pkg/demo_ss_bus_pkg.sv" in trees[0]  # written before MissingRtl


def test_golden_still_stops_on_missing_rtl(tmp_path: Path) -> None:
    with pytest.raises(MissingRtl):
        generate(load(GOLDEN_IR), tmp_path, GOLDEN_IR.parent)
