# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The SV parameter package: fabric widths, decode tables and memory-map constants.

Tables are typed localparams written as assignment patterns (highest index first), so
both Verilator and slang accept them when passed to a parameter.
"""

from hdl_bag.gen.decode import DecodeTables, decode_tables, fabric_offset
from hdl_bag.gen.render import GenerateError, camel, lit
from hdl_bag.ir.model import Fabric, Spec
from hdl_bag.protocols import Protocol

# axi_pkg::xbar_latency_e at the pinned PULP commit (axi v0.39.11, axi_pkg.sv:462-478),
# as 10-bit values so this package compiles before axi_pkg. Bit order, MSB first:
# DemuxAw DemuxW DemuxB | DemuxAr DemuxR | MuxAw MuxW MuxB | MuxAr MuxR.
_DEMUX_AW, _DEMUX_W, _DEMUX_B, _DEMUX_AR, _DEMUX_R = 1 << 9, 1 << 8, 1 << 7, 1 << 6, 1 << 5
_MUX_AW, _MUX_W, _MUX_B, _MUX_AR, _MUX_R = 1 << 4, 1 << 3, 1 << 2, 1 << 1, 1 << 0
LATENCY_MODE = {
    "NO_LATENCY": 0,
    "CUT_SLV_AX": _DEMUX_AW | _DEMUX_AR,
    "CUT_MST_AX": _MUX_AW | _MUX_AR,
    "CUT_ALL_AX": _DEMUX_AW | _DEMUX_AR | _MUX_AW | _MUX_AR,
    "CUT_SLV_PORTS": _DEMUX_AW | _DEMUX_W | _DEMUX_B | _DEMUX_AR | _DEMUX_R,
    "CUT_MST_PORTS": _MUX_AW | _MUX_W | _MUX_B | _MUX_AR | _MUX_R,
    "CUT_ALL_PORTS": (1 << 10) - 1,
}
CUT_STAGES = {"none": 0, "cut": 1, "multicut-2": 2, "multicut-3": 3}


def _pattern(items: list[str]) -> str:
    return "'{" + ", ".join(reversed(items)) + "}"


def _bits(flags: tuple[bool, ...]) -> str:
    return lit(len(flags), sum(1 << i for i, f in enumerate(flags) if f))


def _lp(lines: list[str], typ: str, name: str, value: str) -> None:
    line = f"  localparam {typ} {name} = {value};"
    if len(line) <= 100 or not value.startswith("'{"):
        lines.append(line)
        return
    items = value[2:-1].split(", ")
    lines.append(f"  localparam {typ} {name} = '{{")
    lines += [f"      {item}," for item in items[:-1]] + [f"      {items[-1]}", "  };"]


def fabric_params(p: str, t: DecodeTables) -> list[str]:
    aw, nm, ns = t.addr_width, t.no_masters, t.no_slaves
    nr, na = len(t.rules), len(t.aliases)
    out: list[str] = []

    def table(name: str, width: int, values: list[int]) -> None:
        """[N-1:0][width-1:0] table, one entry per value."""
        typ = f"logic [{len(values) - 1}:0][{width - 1}:0]"
        _lp(out, typ, p + name, _pattern([lit(width, v) for v in values]))

    def bits(name: str, flags: tuple[bool, ...]) -> None:
        _lp(out, f"logic [{len(flags) - 1}:0]", p + name, _bits(flags))

    for key, value in (("AddrWidth", aw), ("NoMasters", nm), ("NoSlaves", ns), ("NoRules", nr),
                       ("NoAliases", na)):  # fmt: skip
        _lp(out, "int unsigned", f"{p}{key}", str(value))
    rules = t.rules
    table("RuleBase", aw, [r.base for r in rules])
    table("RuleMask", aw, [r.mask for r in rules])
    table("RuleSlave", 32, [r.slave for r in rules])
    bits("RuleNoRead", tuple(r.no_read for r in rules))
    bits("RuleNoWrite", tuple(r.no_write for r in rules))
    bits("RuleSecure", tuple(r.secure for r in rules))
    for name, rows in (("Connect", t.connect), ("MstNoRead", t.mst_no_read),
                       ("MstNoWrite", t.mst_no_write)):  # fmt: skip
        typ = f"logic [{nm - 1}:0][{ns - 1}:0]"
        _lp(out, typ, p + name, _pattern([_bits(row) for row in rows]))
    bits("MissToDefault", t.miss_to_default)
    table("DefaultSlave", 32, list(t.default_slave))
    table("Priority", 32, list(t.priority))
    table("AliasMaster", 32, [a.master for a in t.aliases])
    table("AliasBase", aw, [a.base for a in t.aliases])
    table("AliasMask", aw, [a.mask for a in t.aliases])
    table("AliasTo", aw, [a.to for a in t.aliases])
    return out


def user_width(spec: Spec, fab: Fabric) -> int:
    """The widest USER signal among FAB's master and slave ports (0 = no USER)."""
    ports = {m.name: m.user_width for m in spec.masters}
    ports |= {s.name: s.user_width for s in spec.slaves}
    attached = [p.port for p in (*fab.master_ports, *fab.slave_ports)]
    return max((ports.get(n, 0) for n in attached), default=0)


def axi_params(spec: Spec, p: str, fab: Fabric) -> list[str]:
    """Widths and configuration of an AXI4 / AXI4-Lite fabric, for its in-house wrapper."""
    out: list[str] = []

    def lp(key: str, value: int, typ: str = "int unsigned") -> None:
        _lp(out, typ, f"{p}{key}", lit(10, value) if typ != "int unsigned" else str(value))

    lp("DataWidth", fab.data_width)
    if fab.protocol == Protocol.AXI4 and fab.id_width:
        lp("IdWidthMst", fab.id_width.master_side)
        lp("IdWidthSlv", fab.id_width.slave_side)
        lp("UserWidth", user_width(spec, fab))
    if fab.max_mst_trans is not None:
        lp("MaxMstTrans", fab.max_mst_trans)
    if fab.max_slv_trans is not None:
        lp("MaxSlvTrans", fab.max_slv_trans)
    if fab.latency_mode is not None:
        lp("LatencyMode", LATENCY_MODE[fab.latency_mode], "logic [9:0]")
    if fab.midpoint is not None:
        lp("FifoDepth", fab.midpoint.fifo_depth)
        lp("CutStages", CUT_STAGES[fab.midpoint.slice])
    return out


def package(spec: Spec, header: str) -> str:
    name = spec.wrapper.package
    lines = [header.rstrip("\n"), "", f"package {name};", ""]
    for f in spec.fabrics:
        lines.append(f"  // {f.name}: {f.protocol} {f.topology}, template {f.template}")
        offset = fabric_offset(spec, f)
        if offset:
            lines.append(f"  // decoded relative to the pass-through N window at {lit(64, offset)}")
        lines += fabric_params(camel(f.name), decode_tables(f, offset))
        if f.protocol in (Protocol.AXI4, Protocol.AXI4_LITE):
            lines += axi_params(spec, camel(f.name), f)
        lines.append("")
    lines.append("  // Memory map (system addresses)")
    for r in spec.memory_map:
        p = camel(r.slave) + camel(r.region)
        _lp(lines, "logic [63:0]", f"{p}Base", lit(64, r.base))
        _lp(lines, "logic [63:0]", f"{p}Size", lit(64, r.size))
    names = [line.split("=")[0].split()[-1] for line in lines if "localparam" in line]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise GenerateError(
            f"package names collide: {', '.join(dup)} (rename a fabric, slave or region)"
        )
    lines += ["", f"endpackage : {name}", ""]
    return "\n".join(lines)
