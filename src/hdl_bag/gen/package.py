# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The SV parameter package: fabric widths, decode tables and memory-map constants.

Tables are typed localparams written as assignment patterns (highest index first), so
both Verilator and slang accept them when passed to a parameter.
"""

from hdl_bag.gen.decode import DecodeTables, decode_tables
from hdl_bag.gen.render import camel, lit
from hdl_bag.ir.model import Spec


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


def package(spec: Spec, header: str) -> str:
    name = spec.wrapper.package
    lines = [header.rstrip("\n"), "", f"package {name};", ""]
    for f in spec.fabrics:
        lines.append(f"  // {f.name}: {f.protocol} {f.topology}, template {f.template}")
        lines += fabric_params(camel(f.name), decode_tables(f))
        lines.append("")
    lines.append("  // Memory map (system addresses)")
    for r in spec.memory_map:
        p = camel(r.slave) + camel(r.region)
        _lp(lines, "logic [63:0]", f"{p}Base", lit(64, r.base))
        _lp(lines, "logic [63:0]", f"{p}Size", lit(64, r.size))
    lines += ["", f"endpackage : {name}", ""]
    return "\n".join(lines)
