# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Reference model: where does an access from a master go? Written from the IR only.

The spec semantics (workbook guide + owner decisions), not the generator's tables:
per fabric, apply the master's enabled alias, find the Memory_Map region, then deny
on RO/WO, secure-only with a non-secure access, or Access_Rules; a miss or denial
goes to the master's default slave or returns an error. A bridge forwards to its
downstream fabric (as master). Memory_Map addresses are system addresses throughout;
a pass-through N bridge only changes what the slaves behind it see (the offset from
its window base). A port whose PROT is tied is non-secure (D28). A slave sees the low
bits of its address (D29).
"""

from dataclasses import dataclass

from hdl_bag.ir import Spec


@dataclass(frozen=True)
class Outcome:
    slave: str | None  # None: an error response (or a masked one, see masked)
    address: int = 0  # the address as the slave sees it
    masked: bool = False  # the error never reaches the master: it sees OKAY


def prot_tied(spec: Spec, port: str) -> bool:
    opt = next((o for o in spec.port_options if o.port == port), None)
    return opt is not None and opt.prot in ("tie-0", "tie-default")


def pslverr_tied(spec: Spec, port: str) -> bool:
    opt = next((o for o in spec.port_options if o.port == port), None)
    return opt is not None and opt.pslverr in ("tie-0", "tie-default")


def route(spec: Spec, master: str, addr: int, write: bool, nonsec: bool,
          pins: dict[str, bool]) -> Outcome:  # fmt: skip
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    slaves = {s.name: s for s in spec.slaves}
    fabric = next(f for f in spec.fabrics if any(p.port == master for p in f.master_ports))
    nonsec = nonsec or prot_tied(spec, master)
    offset = 0  # subtracted by pass-through N bridges on the way
    masked = pslverr_tied(spec, master)  # a master-side tied PSLVERR hides errors
    for _ in range(len(spec.fabrics)):
        decode = next(d for d in fabric.decode if d.master == master)
        for a in decode.aliases:
            if (
                a.enable == "always" or pins.get(a.enable, False)
            ) and a.base <= addr < a.base + a.size:
                addr = a.to + (addr - a.base)
                break
        target = None
        rule = next((r for r in fabric.addr_rules if r.base <= addr <= r.end), None)
        if rule is not None:
            denied = (write and rule.access == "RO") or (not write and rule.access == "WO")
            denied |= rule.secure and nonsec
            ar = next((a for a in fabric.access_rules
                       if a.master == master and a.slave == rule.slave), None)  # fmt: skip
            if ar is not None:
                denied |= ar.access == "none"
                denied |= (write and ar.access == "RO") or (not write and ar.access == "WO")
            target = None if denied else rule.slave
        if target is None:
            if decode.miss != "default-slave" or decode.default_slave is None:
                return Outcome(None, masked=masked)
            target = decode.default_slave
        if target in bridges:
            b = bridges[target]
            offset += 0 if b.pass_through else b.window_base
            fabric, master = fabrics[b.downstream], b.name
            nonsec = nonsec or prot_tied(spec, b.name)
            masked = masked or pslverr_tied(spec, b.name)
            continue
        width = slaves[target].addr_width
        return Outcome(target, (addr - offset) & ((1 << width) - 1))
    raise AssertionError("bridge chain longer than the number of fabrics")


def probes(spec: Spec) -> list[int]:
    """Interesting addresses: first and last word of each region, the word after it,
    and both ends of every alias window."""
    out: set[int] = set()
    for r in spec.memory_map:
        out |= {r.base, r.base + r.size - 4, r.base + r.size}
    for f in spec.fabrics:
        for d in f.decode:
            for a in d.aliases:
                out |= {a.base, a.base + a.size - 4, a.base + a.size}
    return sorted(a for a in out if a < 1 << 32)
