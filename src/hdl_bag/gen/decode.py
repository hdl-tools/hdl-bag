# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Per-fabric decode tables, independent of the template that consumes them.

The tables follow bag_addr_decode's parameter set: address rules (one per region on the
fabric), per-master connectivity and Access_Rules, miss handling and aliases. Every
table has at least one entry, so a fabric without regions or aliases still elaborates:
a placeholder rule denies everything, and a placeholder alias is never enabled.

A fabric reached through a pass-through N bridge decodes addresses relative to that
bridge's window base: OFFSET is subtracted from every rule and alias address. Regions
below the window cannot be reached through the bridge and are left out.
"""

from dataclasses import dataclass

from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.model import Fabric, Spec


@dataclass(frozen=True)
class Rule:
    base: int
    mask: int  # size - 1
    slave: int  # slave port index; == no_slaves for the placeholder
    no_read: bool
    no_write: bool
    secure: bool


@dataclass(frozen=True)
class Alias:
    master: int
    base: int
    mask: int
    to: int
    enable: str  # "always" or a top-level input


@dataclass(frozen=True)
class DecodeTables:
    addr_width: int
    no_masters: int
    no_slaves: int
    rules: tuple[Rule, ...]
    connect: tuple[tuple[bool, ...], ...]  # [master][slave]
    mst_no_read: tuple[tuple[bool, ...], ...]
    mst_no_write: tuple[tuple[bool, ...], ...]
    miss_to_default: tuple[bool, ...]  # [master]
    default_slave: tuple[int, ...]
    aliases: tuple[Alias, ...]
    priority: tuple[int, ...]  # [master]; 0 when the fabric is not fixed-priority

    @property
    def alias_enables(self) -> tuple[str, ...]:
        """Enable of each alias entry: a top-level input, "1" (always) or "0" (placeholder)."""
        return tuple(
            "0" if not a.enable else "1" if a.enable == "always" else a.enable for a in self.aliases
        )


def fabric_offset(spec: Spec, fabric: Fabric) -> int:
    """Address offset of FABRIC: the window base of the nearest pass-through N bridge on its
    path from the system (a pass-through Y bridge passes its upstream fabric's offset on).

    Every master of a fabric must see the same addresses, so masters that translate
    differently (a pass-through N bridge next to a direct master, or two bridges with
    different windows) cannot share a fabric.
    """
    bridges = {b.name: b for b in spec.bridges}
    fabrics = {f.name: f for f in spec.fabrics}
    offsets = set()
    for p in fabric.master_ports:
        b = bridges.get(p.port)
        if b is None:
            offsets.add(0)
        elif not b.pass_through:
            offsets.add(b.window_base)
        else:
            offsets.add(fabric_offset(spec, fabrics[b.upstream]))
    if len(offsets) > 1:
        raise GenerateError(
            f"fabric {fabric.name}: its masters see different address translations "
            f"(pass-through N bridges with other masters); give them separate fabrics"
        )
    return offsets.pop() if offsets else 0


def bridge_offset(spec: Spec, name: str) -> int:
    """What bridge NAME subtracts from the addresses it forwards: for pass-through N its
    window base relative to its upstream fabric's addresses, for pass-through Y nothing."""
    b = next(x for x in spec.bridges if x.name == name)
    if b.pass_through:
        return 0
    upstream = next(f for f in spec.fabrics if f.name == b.upstream)
    return b.window_base - fabric_offset(spec, upstream)


def decode_tables(fabric: Fabric, offset: int = 0) -> DecodeTables:
    masters = {p.port: p.index for p in fabric.master_ports}
    slaves = {p.port: p.index for p in fabric.slave_ports}
    nm, ns = len(masters), len(slaves)

    rules = [
        Rule(r.base - offset, r.end - r.base, r.slave_port, r.access == "WO", r.access == "RO",
             r.secure)
        for r in fabric.addr_rules
        if r.base >= offset
    ] or [Rule(0, 0, ns, True, True, False)]  # fmt: skip

    connect = [[True] * ns for _ in range(nm)]
    no_read = [[False] * ns for _ in range(nm)]
    no_write = [[False] * ns for _ in range(nm)]
    for a in fabric.access_rules:
        m, s = masters[a.master], slaves[a.slave]
        if a.access == "none":
            connect[m][s] = False
        elif a.access == "RO":
            no_write[m][s] = True
        else:  # WO
            no_read[m][s] = True

    miss = [False] * nm
    default = [0] * nm
    aliases = []
    for d in fabric.decode:
        m = masters[d.master]
        if d.miss == "default-slave" and d.default_slave is not None:
            miss[m], default[m] = True, slaves[d.default_slave]
        aliases += [
            Alias(m, a.base - offset, a.size - 1, a.to - offset, a.enable)
            for a in d.aliases
            if a.base >= offset and a.to >= offset
        ]
    aliases.sort(key=lambda a: (a.master, a.base))

    priority = [0] * nm
    if fabric.arbitration == "fixed-priority":
        for p in fabric.master_ports:
            priority[p.index] = p.priority or 0

    return DecodeTables(
        addr_width=fabric.addr_width,
        no_masters=nm,
        no_slaves=ns,
        rules=tuple(rules),
        connect=tuple(map(tuple, connect)),
        mst_no_read=tuple(map(tuple, no_read)),
        mst_no_write=tuple(map(tuple, no_write)),
        miss_to_default=tuple(miss),
        default_slave=tuple(default),
        aliases=tuple(aliases) or (Alias(0, 0, 0, 0, ""),),
        priority=tuple(priority),
    )
