# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Tool-neutral netlist built from the IR: instances joined by buses.

A ``Bus`` is one AMBA link. External ports are buses that reach the wrapper boundary;
every other bus joins two instances (fabric slot, bridge end, adapter). Signals a
Port_Options row ties off are listed in ``Bus.ties`` with their value: the receiving
side sees the constant and the driving side's output is left unused.
"""

from dataclasses import dataclass, field

from hdl_bag.ir.model import Adapter, Bridge, Fabric, PortOption, Spec
from hdl_bag.protocols import OPTIONAL_SIGNAL_NAMES, Protocol, port_signals, tie_value

# Port_Options field -> optional-signal column
_OPTION_COLUMNS = {
    "prot": "PROT", "qos": "QOS", "region": "REGION", "cache": "CACHE",
    "lock": "LOCK / excl", "pstrb": "PSTRB", "pslverr": "PSLVERR",
}  # fmt: skip


@dataclass(frozen=True)
class Bus:
    name: str
    protocol: Protocol
    addr_width: int
    data_width: int
    id_width: int
    user_width: int
    domain: str
    is_slave: bool  # the attached component acts as a slave on this bus
    signals: tuple[str, ...]  # signals that exist on this bus
    ties: dict[str, int] = field(default_factory=dict)  # tied signal -> value (-1 = all ones)
    external: bool = False  # reaches the wrapper boundary (an exposed master or slave port)


@dataclass
class Instance:
    name: str  # IR name (fabric, bridge or adapter)
    kind: str  # fabric | bridge | adapter
    template: str
    rtl_name: str
    wrapper: str
    fabric: Fabric | None = None
    bridge: Bridge | None = None
    adapter: Adapter | None = None
    masters: list[Bus] = field(default_factory=list)  # fabric master-side slots, by index
    slaves: list[Bus] = field(default_factory=list)  # fabric slave-side slots, by index
    up: Bus | None = None  # bridge / adapter: the side towards the master
    down: Bus | None = None  # bridge / adapter: the side towards the slave


@dataclass
class Netlist:
    spec: Spec
    instances: list[Instance]
    ports: list[Bus]  # external ports, sorted by name


def _ties(option: PortOption | None) -> dict[str, int]:
    if option is None:
        return {}
    out = {}
    for fld, column in _OPTION_COLUMNS.items():
        value = getattr(option, fld)
        if value in ("tie-0", "tie-default"):
            for sig in OPTIONAL_SIGNAL_NAMES[column]:
                out[sig] = tie_value(sig, value)
    return out


def _bus(name: str, protocol: str, aw: int, dw: int, idw: int | None, uw: int, domain: str,
         is_slave: bool, option: PortOption | None, external: bool = False) -> Bus:  # fmt: skip
    proto = Protocol(protocol)
    signals = port_signals(proto, is_slave, has_id=bool(idw), has_user=uw > 0)
    tied = _ties(option)
    ties = {s: tied[s] for s in signals if s in tied}  # protocol order: deterministic
    return Bus(name, proto, aw, dw, idw or 0, uw, domain, is_slave, signals, ties, external)


def build_netlist(spec: Spec) -> Netlist:
    fabrics = {f.name: f for f in spec.fabrics}
    options = {o.port: o for o in spec.port_options}
    placement = {p.instance: p for p in spec.hierarchy.placement}
    exposed = {p.port: p.exposed for p in spec.wrapper.ports}

    def place(name: str) -> tuple[str, str]:
        p = placement.get(name)
        return (p.wrapper, p.rtl_name) if p else (spec.hierarchy.top, f"u_{name}")

    ports: list[Bus] = []
    # The bus at the port end of each (port, fabric) attachment, before any adapter.
    ends: dict[tuple[str, str], Bus] = {}
    for m in spec.masters:
        f = fabrics[m.fabric]
        bus = _bus(m.name, m.protocol, m.addr_width, m.data_width, m.id_width, m.user_width,
                   m.domain, False, options.get(m.name), exposed.get(m.name, True))  # fmt: skip
        ports.append(bus)
        ends[(m.name, f.name)] = bus
    for s in spec.slaves:
        bus = _bus(s.name, s.protocol, s.addr_width, s.data_width, s.id_width, s.user_width,
                   s.domain, True, options.get(s.name), exposed.get(s.name, True))  # fmt: skip
        ports.append(bus)
        ends[(s.name, s.fabric)] = bus

    instances: list[Instance] = []
    for b in spec.bridges:
        up_f, down_f = fabrics[b.upstream], fabrics[b.downstream]
        # A bridge's Port_Options apply to its downstream (master) end, as the checks
        # and the workbook define; the upstream end is a plain slave of its fabric.
        opt = options.get(b.name)
        up = _bus(f"{b.upstream}_2_{b.name}", b.upstream_protocol, up_f.addr_width,
                  b.upstream_width, up_f.id_width.slave_side if up_f.id_width else None, 0,
                  up_f.domain, True, None)  # fmt: skip
        down = _bus(f"{b.name}_2_{b.downstream}", b.downstream_protocol, down_f.addr_width,
                    b.downstream_width, None, 0, down_f.domain, False, opt)  # fmt: skip
        ends[(b.name, b.upstream)] = up
        ends[(b.name, b.downstream)] = down
        wrapper, rtl = place(b.name)
        instances.append(
            Instance(b.name, "bridge", b.template, rtl, wrapper, bridge=b, up=up, down=down)
        )

    # Adapter chains (D9): port -> adapters (chain_index order) -> fabric slot.
    chains: dict[tuple[str, str], list[Adapter]] = {}
    for a in spec.adapters:
        chains.setdefault((a.port, a.fabric), []).append(a)
    for chain in chains.values():
        chain.sort(key=lambda a: a.chain_index)

    def slot(port: str, f: Fabric) -> Bus:
        """The bus that reaches fabric F's slot for PORT, inserting its adapters."""
        cur = ends[(port, f.name)]
        for a in chains.get((port, f.name), []):
            # Each adapter converts one property towards the fabric's (D9). Adapters have
            # RTL from Phase 3; until then this only names and orders the buses.
            proto = Protocol(f.protocol) if a.type == "pc" else cur.protocol
            nxt = _bus(a.name, proto, f.addr_width,
                       f.data_width if a.type == "dw" else cur.data_width, cur.id_width,
                       cur.user_width, f.domain if a.type == "cdc" else cur.domain,
                       cur.is_slave, options.get(port))  # fmt: skip
            wrapper, rtl = place(a.name)
            up, down = (nxt, cur) if cur.is_slave else (cur, nxt)
            instances.append(
                Instance(a.name, "adapter", a.template, rtl, wrapper, adapter=a, up=up, down=down)
            )
            cur = nxt
        return cur

    for f in spec.fabrics:
        wrapper, rtl = place(f.name)
        inst = Instance(f.name, "fabric", f.template, rtl, wrapper, fabric=f)
        inst.masters = [slot(p.port, f) for p in sorted(f.master_ports, key=lambda p: p.index)]
        inst.slaves = [slot(p.port, f) for p in sorted(f.slave_ports, key=lambda p: p.index)]
        instances.append(inst)

    instances.sort(key=lambda i: (i.kind, i.name))
    return Netlist(spec, instances, sorted(ports, key=lambda b: b.name))
