# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Decode properties (Phase 5 PR 3, D69): the address decode of every in-house fabric
against a reference written from the IR.

The reference is the route model's per-fabric decision (``tests_gen.route_model``: aliases,
the Memory_Map region, RO/WO, secure-only, Access_Rules, the miss policy and D32),
evaluated here for each master, region, read or write and secure bit, and written out as
explicit address range compares in system addresses. It never reads the generated
package or the generator's decode tables (``gen.decode``), so a wrong table entry, which
the Phase 4 mutants model, shows as a difference.

The job's top is a generated harness, not the design. Per fabric it instantiates
``bag_addr_decode`` with the fabric's tables from the generated package, as the fabric
does, and drives it from free inputs: the current master (one-hot), the address, read or
write, the secure bit and the alias-enable pins. Combinational assertions then require
its slave select, error and aliased address to equal the reference for every input, so
the check is exhaustive, not bounded. On AXI fabrics the error a master gets (DECERR or
SLVERR, from the package's MissResp) is checked too.
"""

from dataclasses import dataclass
from typing import Any

from hdl_bag.gen.decode import decode_tables, fabric_offset
from hdl_bag.gen.netlist import Instance, Netlist
from hdl_bag.gen.render import camel, lit
from hdl_bag.ir.model import Fabric, Spec
from hdl_bag.protocols import Protocol
from hdl_bag.templates import builtin_registry
from hdl_bag.tests_gen.route_model import denied, fallback

DECODER = "rtl/inhouse/bag_addr_decode.sv"
# The error a master's guard answers, as the generated tests expect it: DECERR for a miss
# with no default slave unless SLVERR is chosen, and DECERR for a default-slave master's own
# denials (D32).
_RESP = {"SLVERR": 2, "DECERR": 3, "default-slave": 3}


@dataclass(frozen=True)
class Target:
    """Where a master's access goes: a slave port index of the fabric, or None (error)."""

    port: int | None


def decoded_fabrics(netlist: Netlist) -> list[Instance]:
    """The fabric instances that decode through bag_addr_decode, in netlist order."""
    registry = builtin_registry()
    out = []
    for inst in netlist.instances:
        manifest = registry.get(inst.template)
        if inst.kind == "fabric" and manifest is not None and DECODER in manifest.sources:
            out.append(inst)
    return out


def _ports(fabric: Fabric) -> tuple[dict[str, int], dict[str, int]]:
    masters = {p.port: p.index for p in fabric.master_ports}
    slaves = {p.port: p.index for p in fabric.slave_ports}
    return masters, slaves


def _target(fabric: Fabric, master: str, region: Any | None, write: bool, nonsec: bool) -> int:
    """The reference's slave port index for MASTER's access to REGION (None: a miss), or the
    number of slave ports for an error."""
    _, slaves = _ports(fabric)
    if region is not None and not denied(fabric, master, region, write, nonsec):
        return slaves[str(region.slave)]
    to = fallback(fabric, master, region)
    return len(slaves) if to is None else slaves[to]


def _cases(name: str, fabric: Fabric, master: str, region: Any | None) -> list[str]:
    """``tgt`` for each read/write and secure bit, as a case on {write, nonsec}."""
    rows = []
    for write in (False, True):
        for nonsec in (False, True):
            t = _target(fabric, master, region, write, nonsec)
            rows.append(f"2'b{int(write)}{int(nonsec)}: {name}_tgt = {t};")
    body = " ".join(rows)
    return [f"        case ({{{name}_write, {name}_nonsec}}) {body} endcase"]


def _reference(spec: Spec, inst: Instance, name: str) -> list[str]:
    """always_comb computing NAME_ref_addr (aliased), NAME_tgt (slave port, or the slave
    count for an error) from NAME_mst, NAME_addr, NAME_write, NAME_nonsec and the pins."""
    fab = inst.fabric
    assert fab is not None
    aw = fab.addr_width
    off = fabric_offset(spec, fab)
    masters, _ = _ports(fab)
    regions = [r for r in spec.memory_map if r.fabric == fab.name]
    out = [
        f"  // {fab.name}: reference from the IR (system address = fabric address + "
        f"{lit(64, off)})",
        f"  logic [64:0] {name}_sys, {name}_hit_sys;",
        f"  logic [{aw - 1}:0] {name}_ref_addr;",
        f"  int unsigned {name}_tgt;",
        "  always_comb begin",
        f"    {name}_sys = 65'({name}_addr) + 65'({lit(64, off)});",
        f"    {name}_hit_sys = {name}_sys;",
        f"    {name}_tgt = {len(fab.slave_ports)};",
    ]
    for master, m in sorted(masters.items(), key=lambda kv: kv[1]):
        decode = next(d for d in fab.decode if d.master == master)
        out.append(f"    if ({name}_mst[{m}]) begin  // {master}")
        first = True
        for a in decode.aliases:
            en = "1'b1" if a.enable == "always" else a.enable
            kw = "if" if first else "else if"
            first = False
            out += [
                f"      {kw} ({en} && {name}_sys >= 65'({lit(64, a.base)}) && "
                f"{name}_sys < 65'({lit(64, a.base + a.size)}))",
                f"        {name}_hit_sys = 65'({lit(64, a.to)}) + ({name}_sys - "
                f"65'({lit(64, a.base)}));",
            ]
        kw = "if"
        for r in regions:
            out.append(
                f"      {kw} ({name}_hit_sys >= 65'({lit(64, r.base)}) && "
                f"{name}_hit_sys < 65'({lit(64, r.base + r.size)})) begin  // {r.slave}.{r.region}"
            )
            out += _cases(name, fab, master, r)
            out.append("      end")
            kw = "else if"
        if regions:
            out.append("      else begin  // a miss")
            out += _cases(name, fab, master, None)
            out.append("      end")
        else:
            out += _cases(name, fab, master, None)
        out.append("    end")
    out += [
        f"    {name}_ref_addr = {aw}'({name}_hit_sys - 65'({lit(64, off)}));",
        "  end",
    ]
    return out


def _alias_en(spec: Spec, fab: Fabric) -> str:
    """The decoder's alias-enable vector, highest alias first, as the fabric's instance
    template builds it (gen.wrapper.InstanceContext.alias_en)."""
    enables = decode_tables(fab, fabric_offset(spec, fab)).alias_enables
    bits = [f"1'b{e}" if e in ("0", "1") else e for e in reversed(enables)]
    return bits[0] if len(bits) == 1 else "{" + ", ".join(bits) + "}"


def _fabric(spec: Spec, inst: Instance, pkg: str) -> tuple[list[str], list[str]]:
    """(harness ports, body) for one decoded fabric."""
    fab = inst.fabric
    assert fab is not None
    name = inst.name
    p = f"{pkg}::{camel(inst.name)}"
    aw, nm, ns = fab.addr_width, len(fab.master_ports), len(fab.slave_ports)
    ports = [
        f"input logic [{nm - 1}:0] {name}_mst",
        f"input logic [{aw - 1}:0] {name}_addr",
        f"input logic {name}_write",
        f"input logic {name}_nonsec",
    ]
    params = ["AddrWidth", "NoMasters", "NoSlaves", "NoRules", "NoAliases", "RuleBase",
              "RuleMask", "RuleSlave", "RuleNoRead", "RuleNoWrite", "RuleSecure", "Connect",
              "MstNoRead", "MstNoWrite", "MissToDefault", "DefaultSlave", "AliasMaster",
              "AliasBase", "AliasMask", "AliasTo"]  # fmt: skip
    conns = ",\n".join(f"      .{k:<13} ({p}{k})" for k in params)
    body = [
        f"  // {fab.name}: {fab.protocol} {fab.topology}; the decoder as the fabric has it",
        f"  always_comb assume ({name}_mst != '0 && ({name}_mst & ({name}_mst - 1'b1)) == '0);",
        f"  logic [{aw - 1}:0] {name}_dec_addr;",
        f"  logic [{ns - 1}:0] {name}_dec_sel;",
        f"  logic {name}_dec_err;",
        "  bag_addr_decode #(",
        conns,
        f"  ) u_{name}_decode (",
        f"      .mst_i     ({name}_mst),",
        f"      .addr_i    ({name}_addr),",
        f"      .write_i   ({name}_write),",
        f"      .nonsec_i  ({name}_nonsec),",
        f"      .alias_en_i({_alias_en(spec, fab)}),",
        f"      .addr_o    ({name}_dec_addr),",
        f"      .sel_o     ({name}_dec_sel),",
        f"      .err_o     ({name}_dec_err)",
        "  );",
        *_reference(spec, inst, name),
        "  always_comb begin",
        f"    {name}_selects: assert ({name}_dec_sel == ({name}_tgt < {ns} ? "
        f"{ns}'(1) << {name}_tgt : {ns}'(0)));",
        f"    {name}_errors: assert ({name}_dec_err == ({name}_tgt == {ns}));",
        f"    {name}_aliases: assert ({name}_dec_addr == {name}_ref_addr);",
    ]
    if fab.protocol in (Protocol.AXI4, Protocol.AXI4_LITE):
        masters, _ = _ports(fab)
        miss = {d.master: d.miss for d in fab.decode}
        for master, m in sorted(masters.items(), key=lambda kv: kv[1]):
            want = _RESP.get(miss.get(master, "DECERR"), _RESP["DECERR"])
            body.append(f"    if ({name}_tgt == {ns} && {name}_mst[{m}]) {name}_miss_response_{m}: "
                        f"assert ({p}MissResp[{m}] == {lit(2, want)});  // {master}")  # fmt: skip
    body += ["  end", ""]
    return ports, body


def render_decode(spec: Spec, netlist: Netlist, hdr: str) -> str | None:
    """The decode harness for SPEC (module bag_<project>_decode); None with no in-house
    fabric that decodes."""
    fabrics = decoded_fabrics(netlist)
    if not fabrics:
        return None
    pkg = spec.wrapper.package
    ports: list[str] = []
    body: list[str] = []
    pins = sorted({a.enable for f in fabrics if f.fabric for d in f.fabric.decode
                   for a in d.aliases if a.enable != "always"})  # fmt: skip
    ports += [f"input logic {pin}" for pin in pins]
    for inst in fabrics:
        p, b = _fabric(spec, inst, pkg)
        ports += p
        body += b
    name = f"bag_{spec.project.name}_decode"
    lines = [
        hdr.rstrip("\n"),
        "",
        f"// Decode properties of {spec.project.name} (D69): each in-house fabric's",
        "// bag_addr_decode, fed from the generated package, against a reference from the IR.",
        f"module {name} (",
        *(f"    {pt}," for pt in ports[:-1]),
        f"    {ports[-1]}",
        ");",
        "",
        *body,
        "endmodule",
        "",
    ]
    return "\n".join(lines)
