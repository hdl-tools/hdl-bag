# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The configuration-space dimensions of one config (plan PR 5), read from its IR.

Every regress result records them, so PR 7's coverage report can say which combinations
have passed. They come from the resolved IR rather than the draw, so a corpus config gets
the same dimensions as a random one.
"""

from itertools import pairwise

from hdl_bag.ir.model import Master, Slave, Spec

Dimensions = dict[str, int | bool | str | list[str]]


def _depth(spec: Spec) -> int:
    parent = {w.name: w.parent for w in spec.hierarchy.wrappers}

    def depth(name: str) -> int:
        return 1 + depth(parent[name]) if name in parent else 0

    return max((depth(w) for w in parent), default=0)


def _sparse(spec: Spec) -> bool:
    """True when any fabric's decode leaves a gap between two consecutive regions."""
    for fab in spec.fabrics:
        spans = sorted((r.base, r.end) for r in fab.addr_rules)
        if any(b[0] > a[1] + 1 for a, b in pairwise(spans)):
            return True
    return False


def dimensions(spec: Spec) -> Dimensions:
    """The dimensions of SPEC: counts, and sorted lists of what it combines."""
    # Width ratios as port (or upstream) : fabric (or downstream) data width.
    ratios = {f"{b.upstream_width}:{b.downstream_width}" for b in spec.bridges
              if b.upstream_width != b.downstream_width}  # fmt: skip
    ports: dict[str, Master | Slave] = {m.name: m for m in spec.masters}
    ports |= {s.name: s for s in spec.slaves}
    fabrics = {f.name: f for f in spec.fabrics}
    for a in spec.adapters:
        if a.type == "dw" and a.port in ports:
            p = ports[a.port]
            ratios.add(f"{p.data_width}:{fabrics[p.fabric].data_width}")
    # Bridges join fabrics (">"); protocol adapters join a port to its fabric ("~").
    pairs = {f"{b.upstream_protocol}>{b.downstream_protocol}" for b in spec.bridges}
    pairs |= {f"{ports[a.port].protocol}~{fabrics[a.fabric].protocol}" for a in spec.adapters
              if a.type == "pc" and a.port in ports}  # fmt: skip
    options = set()
    for o in spec.port_options:
        if o.fifo_depth:
            options.add("fifo")
        if o.slice not in ("none", "n/a"):
            options.add("slice")
        signals = {o.prot, o.qos, o.region, o.cache, o.lock, o.pstrb, o.pslverr}
        if signals & {"tie-0", "tie-default"}:
            options.add("tie")
    return {
        "fabrics": len(spec.fabrics),
        "masters": len(spec.masters),
        "slaves": len(spec.slaves),
        "bridges": sorted(b.template for b in spec.bridges),
        "topologies": sorted({f"{f.protocol} {f.topology}" for f in spec.fabrics}),
        "protocol_pairs": sorted(pairs),
        "width_ratios": sorted(ratios),
        "domains": len(spec.clocks),
        "clock_crossings": sum(a.type == "cdc" for a in spec.adapters)
        + sum(b.cdc for b in spec.bridges),
        "map": "sparse" if _sparse(spec) else "dense",
        "aliases": sum(len(d.aliases) for f in spec.fabrics for d in f.decode),
        "secure": any(r.secure for r in spec.memory_map),
        "access_rules": sum(len(f.access_rules) for f in spec.fabrics),
        "hierarchy_depth": _depth(spec),
        "user_ports": len(spec.user_ports),
        "port_options": sorted(options),
        "external": any(f.template == "bag_external" for f in spec.fabrics),
        "port_style": spec.wrapper.port_style,
    }
