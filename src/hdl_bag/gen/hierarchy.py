# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Sub-wrapper placement and routing: which buses cross which wrapper boundaries.

Every instance lives in one wrapper (Hierarchy placement, defaults applied by resolve).
A bus is a net in the lowest common wrapper of its two ends; each wrapper on the way
down to an end gets boundary ports for it, named by Wrapper > Sub-wrapper port naming
with ``{instance}`` = the bus name. An exposed master or slave port has its outer end
at the top boundary. Tied signals are not routed: the receiving side's wrapper sees the
constant. Clocks, conditioned resets and extra top-level inputs are routed down to the
wrappers whose instances use them (see wrapper.py); reset conditioning stays at the top.

This module is pure (no templates, no RTL), so ``bag check`` can use it to report
boundary-name collisions before anything is generated.
"""

from dataclasses import dataclass, field

from hdl_bag.gen.netlist import Bus, Instance, Netlist
from hdl_bag.ir.model import Spec
from hdl_bag.naming import subwrapper_signal_name
from hdl_bag.protocols import PROTO_TOKEN, towards_slave


@dataclass(frozen=True)
class Boundary:
    """A bus crossing a sub-wrapper's boundary."""

    bus: Bus
    inside_master: bool  # the bus's master end is inside the wrapper

    def signals(self) -> tuple[str, ...]:
        """Signals that become boundary ports (tied ones stay with their receiver)."""
        return tuple(s for s in self.bus.signals if s not in self.bus.ties)

    def direction(self, sig: str) -> str:
        """Port direction of SIG on the wrapper."""
        driven_by_master = towards_slave(sig, self.bus.is_slave)
        return "output" if driven_by_master == self.inside_master else "input"


@dataclass
class Plan:
    top: str
    parent: dict[str, str]  # sub-wrapper -> parent
    children: dict[str, list[str]] = field(default_factory=dict)  # sorted
    instances: dict[str, list[Instance]] = field(default_factory=dict)
    boundaries: dict[str, list[Boundary]] = field(default_factory=dict)  # sorted by bus name

    def wrappers(self) -> list[str]:
        """Every wrapper, children before parents (compile order), the top last."""
        out: list[str] = []

        def visit(w: str) -> None:
            for c in self.children.get(w, []):
                visit(c)
            out.append(w)

        visit(self.top)
        return out

    def path_up(self, wrapper: str) -> list[str]:
        """WRAPPER and its ancestors, up to and including the top."""
        out = [wrapper]
        while out[-1] != self.top:
            out.append(self.parent[out[-1]])
        return out


def _ends(netlist: Netlist) -> dict[str, list[tuple[str, bool]]]:
    """Bus name -> its ends as (wrapper, is the master end)."""
    top = netlist.spec.hierarchy.top
    ends: dict[str, list[tuple[str, bool]]] = {}
    for bus in netlist.ports:
        if bus.external:  # the outer end is outside the top wrapper
            ends.setdefault(bus.name, []).append((top, not bus.is_slave))
    for inst in netlist.instances:
        for bus in inst.masters:  # a fabric is the slave end of its master-side slots
            ends.setdefault(bus.name, []).append((inst.wrapper, False))
        for bus in inst.slaves:
            ends.setdefault(bus.name, []).append((inst.wrapper, True))
        if inst.up is not None:  # bridges and adapters: slave end of up, master of down
            ends.setdefault(inst.up.name, []).append((inst.wrapper, False))
        if inst.down is not None:
            ends.setdefault(inst.down.name, []).append((inst.wrapper, True))
    return ends


def plan(netlist: Netlist) -> Plan:
    spec = netlist.spec
    top = spec.hierarchy.top
    p = Plan(top, {w.name: w.parent for w in spec.hierarchy.wrappers})
    for w in [top, *p.parent]:
        p.children[w] = sorted(c for c, par in p.parent.items() if par == w)
        p.instances[w] = []
        p.boundaries[w] = []
    for inst in netlist.instances:
        p.instances[inst.wrapper].append(inst)
    buses = {b.name: b for b in netlist.ports}
    for inst in netlist.instances:
        for b in (*inst.masters, *inst.slaves, inst.up, inst.down):
            if b is not None:
                buses[b.name] = b
    for name, ends in sorted(_ends(netlist).items()):
        paths = [p.path_up(w) for w, _ in ends]
        lca = next(w for w in paths[0] if all(w in path for path in paths))
        for (_, is_master), path in zip(ends, paths, strict=True):
            for w in path[: path.index(lca)]:
                p.boundaries[w].append(Boundary(buses[name], is_master))
    return p


def boundary_port(spec: Spec, bus: Bus, sig: str) -> str:
    return subwrapper_signal_name(
        spec.wrapper.subwrapper_port_naming, bus.name, PROTO_TOKEN[bus.protocol], sig
    )


def boundary_names(netlist: Netlist, p: Plan) -> dict[str, dict[str, list[str]]]:
    """Per sub-wrapper: port name -> the buses (or clocks, resets, inputs) claiming it.

    Clocks, resets and inputs are claimed by every sub-wrapper here (a superset of what
    is routed), so a collision with one of them is reported even if not yet used.
    """
    spec = netlist.spec
    fixed = [(c.name, f"clock {c.name}") for c in spec.clocks]
    fixed += [(f"bag_rst_{c.domain}_n", f"the reset of {c.domain}") for c in spec.clocks]
    fixed += [(x, f"extra input {x}") for x in spec.wrapper.extra_inputs]
    out: dict[str, dict[str, list[str]]] = {}
    for w, crossings in p.boundaries.items():
        if w == p.top:
            continue
        claims: dict[str, list[str]] = {n: [what] for n, what in fixed}
        for b in crossings:
            for sig in b.signals():
                claims.setdefault(boundary_port(spec, b.bus, sig), []).append(f"link {b.bus.name}")
        out[w] = claims
    return out


def collisions(netlist: Netlist, p: Plan) -> list[tuple[str, str, list[str]]]:
    """(sub-wrapper, port name, claimants) for every boundary port claimed twice."""
    return [
        (w, name, owners)
        for w, claims in sorted(boundary_names(netlist, p).items())
        for name, owners in sorted(claims.items())
        if len(owners) > 1
    ]
