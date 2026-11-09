# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Decode properties (Phase 5 PR 3, D69): the address decode of every in-house fabric
against a reference written from the IR.

The reference is the route model's per-fabric decision (``tests_gen.route_model``: aliases,
the Memory_Map region, RO/WO, secure-only, Access_Rules, the miss policy and D32, and the
fabric's address window), evaluated here for each master, region, read or write and secure
bit, and written out as two SystemVerilog functions per fabric with explicit address range
compares in system addresses: ``<fabric>_aliased`` (an address after the master's aliases)
and ``<fabric>_target`` (the slave port an aliased address goes to, or the slave count for
an error). It never reads the generated package or the generator's decode tables
(``gen.decode``), so a wrong table entry, which the Phase 4 mutants model, shows as a
difference. The route jobs (``formal_gen.units``) use the same functions.

The decode job's top is a generated harness, not the design. Per fabric it instantiates
``bag_addr_decode`` with the fabric's tables from the generated package, as the fabric
does, and drives it from free inputs: the current master (one-hot), the address, read or
write, the secure bit and the alias-enable pins. Combinational assertions then require
its slave select, error and aliased address to equal the reference for every input, so
the check is exhaustive, not bounded. On AXI fabrics the error a master gets (DECERR or
SLVERR, from the package's MissResp) is checked too.
"""

from typing import Any

from hdl_bag.gen.decode import decode_tables, fabric_offset
from hdl_bag.gen.netlist import Instance, Netlist
from hdl_bag.gen.render import camel, lit
from hdl_bag.ir.model import Fabric, Spec
from hdl_bag.protocols import Protocol
from hdl_bag.templates import builtin_registry
from hdl_bag.tests_gen.route_model import denied, fallback, window

DECODER = "rtl/inhouse/bag_addr_decode.sv"
# The error a master's guard answers, as the generated tests expect it: DECERR for a miss
# with no default slave unless SLVERR is chosen, and DECERR for a default-slave master's own
# denials (D32).
_RESP = {"SLVERR": 2, "DECERR": 3, "default-slave": 3}


def decoded_fabrics(netlist: Netlist) -> list[Instance]:
    """The fabric instances that decode through bag_addr_decode, in netlist order."""
    registry = builtin_registry()
    out = []
    for inst in netlist.instances:
        manifest = registry.get(inst.template)
        if inst.kind == "fabric" and manifest is not None and DECODER in manifest.sources:
            out.append(inst)
    return out


def masters_of(fabric: Fabric) -> list[str]:
    """FABRIC's master ports, by Port index."""
    return [p.port for p in sorted(fabric.master_ports, key=lambda p: p.index)]


def _target(fabric: Fabric, master: str, region: Any | None, write: bool, nonsec: bool) -> int:
    """The reference's slave port index for MASTER's access to REGION (None: a miss), or the
    number of slave ports for an error."""
    slaves = {p.port: p.index for p in fabric.slave_ports}
    if region is not None and not denied(fabric, master, region, write, nonsec):
        return slaves[str(region.slave)]
    to = fallback(fabric, master, region)
    return len(slaves) if to is None else slaves[to]


def port_accesses(spec: Spec, fabric: Fabric) -> list[frozenset[str]]:
    """Per slave port of FABRIC (by Port index), the accesses ("read", "write") the reference
    sends there for some master, address and secure bit: a master may send any address, so
    every region and a miss are tried."""
    regions: list[Any] = [r for r in spec.memory_map if r.fabric == fabric.name]
    out: list[set[str]] = [set() for _ in fabric.slave_ports]
    for master in masters_of(fabric):
        for region in [*regions, None]:
            for write in (False, True):
                for nonsec in (False, True):
                    k = _target(fabric, master, region, write, nonsec)
                    if k < len(out):
                        out[k].add("write" if write else "read")
    return [frozenset(s) for s in out]


def _case(fabric: Fabric, master: str, region: Any | None) -> str:
    """The target for each read/write and secure bit, as a case on {write, nonsec}."""
    rows = [f"2'b{int(w)}{int(n)}: return {_target(fabric, master, region, w, n)};"
            for w in (False, True) for n in (False, True)]  # fmt: skip
    return f"case ({{write, nonsec}}) {' '.join(rows)} endcase"


def _in(var: str, base: int, size: int) -> str:
    """VAR (a 65-bit system address) lies in [BASE, BASE + SIZE); the end may be 2**64."""
    end = base + size
    hi = f"65'({lit(64, end)})" if end < 1 << 64 else "65'h1_0000_0000_0000_0000"
    return f"{var} >= 65'({lit(64, base)}) && {var} < {hi}"


def reference(spec: Spec, fabric: Fabric, name: str) -> list[str]:
    """The reference functions NAME_aliased and NAME_target for FABRIC (see the module
    docstring). Alias-enable pins are read by name from the enclosing module."""
    aw, off = fabric.addr_width, window(spec, fabric)
    regions = [r for r in spec.memory_map if r.fabric == fabric.name]
    out = [
        f"  // {fabric.name}: the reference from the IR; system address = fabric address + "
        f"{lit(64, off)}",
        f"  function automatic logic [{aw - 1}:0] {name}_aliased(int unsigned m, "
        f"logic [{aw - 1}:0] addr);",
        f"    logic [64:0] sys = 65'(addr) + 65'({lit(64, off)});",
    ]
    for m, master in enumerate(masters_of(fabric)):
        decode = next(d for d in fabric.decode if d.master == master)
        for a in decode.aliases:
            en = "1'b1" if a.enable == "always" else a.enable
            out.append(f"    if (m == {m} && {en} && {_in('sys', a.base, a.size)})  // {master}")
            out.append(f"      return {aw}'(65'({lit(64, a.to)}) + (sys - 65'({lit(64, a.base)})) "
                       f"- 65'({lit(64, off)}));")  # fmt: skip
    out += [
        "    return addr;",
        "  endfunction",
        f"  function automatic int unsigned {name}_target(int unsigned m, "
        f"logic [{aw - 1}:0] addr, logic write, logic nonsec);",
        f"    logic [64:0] sys = 65'(addr) + 65'({lit(64, off)});",
    ]
    for m, master in enumerate(masters_of(fabric)):
        out.append(f"    if (m == {m}) begin  // {master}")
        for r in regions:
            out.append(f"      if ({_in('sys', r.base, r.size)})  // {r.slave}.{r.region}")
            out.append(f"        {_case(fabric, master, r)}")
        out.append(f"      {_case(fabric, master, None)}  // a miss")
        out.append("    end")
    out += [f"    return {len(fabric.slave_ports)};", "  endfunction"]
    return out


def alias_pins(fabric: Fabric) -> list[str]:
    """The alias-enable pins FABRIC's decode reads."""
    return sorted({a.enable for d in fabric.decode for a in d.aliases if a.enable != "always"})


def _alias_en(spec: Spec, fab: Fabric) -> str:
    """The decoder's alias-enable vector, highest alias first, as the fabric's instance
    template builds it (gen.wrapper.InstanceContext.alias_en): harness wiring, not part of
    the reference."""
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
        *reference(spec, fab, name),
        f"  logic [{aw - 1}:0] {name}_ref_addr;",
        f"  int unsigned {name}_tgt;",
        "  always_comb begin",
        f"    {name}_ref_addr = {name}_addr;",
        f"    {name}_tgt = {ns};",
        f"    for (int unsigned m = 0; m < {nm}; m++) begin",
        f"      if ({name}_mst[m]) begin",
        f"        {name}_ref_addr = {name}_aliased(m, {name}_addr);",
        f"        {name}_tgt = {name}_target(m, {name}_ref_addr, {name}_write, {name}_nonsec);",
        "      end",
        "    end",
        "  end",
        "  always_comb begin",
        f"    {name}_selects: assert ({name}_dec_sel == ({name}_tgt < {ns} ? "
        f"{ns}'(1) << {name}_tgt : {ns}'(0)));",
        f"    {name}_errors: assert ({name}_dec_err == ({name}_tgt == {ns}));",
        f"    {name}_aliases: assert ({name}_dec_addr == {name}_ref_addr);",
    ]
    if fab.protocol in (Protocol.AXI4, Protocol.AXI4_LITE):
        miss = {d.master: d.miss for d in fab.decode}
        for m, master in enumerate(masters_of(fab)):
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
    pins = sorted({p for f in fabrics if f.fabric for p in alias_pins(f.fabric)})
    ports: list[str] = [f"input logic {pin}" for pin in pins]
    body: list[str] = []
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
