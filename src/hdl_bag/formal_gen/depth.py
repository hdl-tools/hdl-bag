# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Depth per port path for the protocol job (D77, Phase 5 PR 4b).

A port's covers need a transfer between it and a peer: a slave port, a master that reaches it;
a master port, a slave it reaches (the route model decides which, over its probe addresses,
both security states and every alias pin setting). The port's depth is the cost of the
shortest netlist path to such a peer, and never below PROTOCOL_DEPTH (16, D77):

- PATH_EDGES clock edges for a path through one component (a fabric, bridge or adapter), as a
  route job of one unit needs (ROUTE_CYCLES), plus PATH_EDGES_PER_HOP per further component
  and PATH_EDGES_PER_HALVING per halving of the data width on the way;
- a design in one clock domain checks its ports on one clock, so the depth in solver steps is
  that many edges; with free clocks each edge takes two steps, and each clock-domain crossing
  on the path CDC_STEPS more (route jobs: one crossing, no conversion, reached its last cover
  at step 21 of an 8-edge unit).

Each port's cover task runs at its own depth; the protocol bmc stays at PROTOCOL_DEPTH on every
design (D79, owner 2026-11-12: at the deepest port's depth the multi-domain designs' bmc would
not finish), so a cover reached past step 16 shows the check can fire there, not that the bmc
checked that far. The constants are a first estimate, tuned from the formal corpus's nightly
runs (tests/formal_gen/test_formal_corpus.py).
"""

from collections import deque

from hdl_bag.gen.netlist import Bus, Instance, Netlist
from hdl_bag.ir.model import Spec

PROTOCOL_DEPTH = 16
PATH_EDGES = 8
PATH_EDGES_PER_HOP = 4
PATH_EDGES_PER_HALVING = 2
CDC_STEPS = 8


def reaching(spec: Spec) -> dict[tuple[str, str], set[str]]:
    """The (master, slave) pairs where some access of the master reaches the slave, each with
    the kinds of access ("read", "write") that do."""
    from hdl_bag.tests_gen.route_model import pin_sets, probes, route

    out: dict[tuple[str, str], set[str]] = {}
    addrs = probes(spec)
    for m in spec.masters:
        for addr in (a for a in addrs if a < 1 << m.addr_width):
            for write in (False, True):
                for nonsec in (False, True):
                    for pins in pin_sets(spec):
                        slave = route(spec, m.name, addr, write, nonsec, pins).slave
                        if slave is not None:
                            out.setdefault((m.name, slave), set()).add("write" if write else "read")
    return out


def reachable(spec: Spec) -> set[tuple[str, str]]:
    """The (master, slave) pairs where some access of the master reaches the slave."""
    return set(reaching(spec))


def _buses(inst: Instance) -> list[Bus]:
    found = [*inst.masters, *inst.slaves] if inst.kind == "fabric" else [inst.up, inst.down]
    return [b for b in found if b is not None]


def path(netlist: Netlist, a: str, b: str) -> list[tuple[Bus, Instance, Bus]] | None:
    """The shortest path from bus A to bus B: each step a component and the buses it joins."""
    adj: dict[str, list[tuple[Bus, Instance, Bus]]] = {}
    for inst in netlist.instances:
        buses = _buses(inst)
        for x in buses:
            adj.setdefault(x.name, []).extend((x, inst, y) for y in buses if y.name != x.name)
    seen: dict[str, list[tuple[Bus, Instance, Bus]]] = {a: []}
    queue = deque([a])
    while queue:
        n = queue.popleft()
        if n == b:
            return seen[n]
        for x, inst, y in adj.get(n, []):
            if y.name not in seen:
                seen[y.name] = [*seen[n], (x, inst, y)]
                queue.append(y.name)
    return None


def path_steps(steps: list[tuple[Bus, Instance, Bus]], multi: bool) -> int:
    """The solver steps a transfer along STEPS needs (see the module docstring)."""
    halvings = sum(max(x.data_width, y.data_width).bit_length()
                   - min(x.data_width, y.data_width).bit_length() for x, _, y in steps)  # fmt: skip
    edges = PATH_EDGES + PATH_EDGES_PER_HOP * (len(steps) - 1) + PATH_EDGES_PER_HALVING * halvings
    if not multi:
        return edges
    crossings = sum(x.domain != y.domain for x, _, y in steps)
    return 2 * edges + CDC_STEPS * crossings


def _cost(spec: Spec, netlist: Netlist, a: str, b: str) -> int | None:
    """The solver steps a transfer between ports A and B needs, if a path joins them."""
    multi = len({c.domain for c in spec.clocks}) > 1
    p = path(netlist, a, b)
    return path_steps(p, multi) if p else None


def port_depths(spec: Spec, netlist: Netlist, ports: list[str]) -> dict[str, int]:
    """Each of PORTS (external port names) with its protocol depth in solver steps."""
    pairs = reachable(spec)
    out = {}
    for port in ports:
        peers = [s for m, s in pairs if m == port] + [m for m, s in pairs if s == port]
        costs = [c for q in sorted(set(peers))
                 if (c := _cost(spec, netlist, port, q)) is not None]  # fmt: skip
        out[port] = max(PROTOCOL_DEPTH, min(costs, default=PROTOCOL_DEPTH))
    return out


def active_masters(spec: Spec, netlist: Netlist, port: str,
                   pairs: dict[tuple[str, str], set[str]]) -> list[str]:  # fmt: skip
    """The masters PORT's cover task keeps active (D82), the others held idle: a master port,
    itself; a slave port, for each kind of access the IR sends it (PAIRS, from reaching), the
    nearest master making it (by path cost, then name), so each of its covers still has a
    master to make it."""
    if port in {m.name for m in spec.masters}:
        return [port]
    keep = set()
    for kind in ("read", "write"):
        ranked = sorted((c, m) for (m, s), kinds in pairs.items() if s == port and kind in kinds
                        if (c := _cost(spec, netlist, m, port)) is not None)  # fmt: skip
        if ranked:
            keep.add(ranked[0][1])
    return sorted(keep)
