# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Which converters refuse which AXI4 bursts (D34), from the IR.

PULP's width converters and the AXI4-to-Lite protocol converter cannot split a (legal, 2 to
16 beat) WRAP burst, nor a multi-beat FIXED burst into a narrower bus, so they answer those
with SLVERR. The manifests of bag_dw, bag_pc and the bridges say the same. The generated
tests expect SLVERR wherever ``refusing_converter`` says so.

``bag check`` warns about WRAP only where a width converter refuses it (D49, narrowed by the
owner): a dw adapter, or bag_axi2ahb between different widths, where a WRAP burst such as a
cache-line fill plausibly targets memory. Conversion to AXI4-Lite or APB (a pc adapter,
bag_axi2axil, bag_axi2apb) is a deliberate move to single-beat register buses, where WRAP is
rare, so it does not warn; it still refuses.
"""

from collections.abc import Sequence
from typing import Final

from hdl_bag.ir.model import Spec

FIXED: Final = 0
INCR: Final = 1
WRAP: Final = 2


def refusing_converter(
    spec: Spec, master: str, bridges: Sequence[str], slave: str | None, burst: int, beats: int
) -> str | None:
    """The adapter or bridge that answers this AXI4 burst with SLVERR on the way from MASTER
    through BRIDGES (in order) to SLAVE (None when a fabric answers), or None if it gets
    through. Once a path has become single transfers (AXI4-Lite, AHB-Lite, APB), what follows
    only ever sees those."""

    def by_width(wide: int, narrow: int) -> bool:
        return burst == WRAP or (burst == FIXED and beats > 1 and wide > narrow)

    def adapters(port: str, towards_port: bool) -> tuple[str | None, bool]:
        """Walk PORT's adapters in the direction of travel: (the one refusing, or None; whether
        the burst is single transfers from there on)."""
        chain = sorted((a for a in spec.adapters if a.port == port), key=lambda a: a.chain_index)
        for a in reversed(chain) if towards_port else chain:
            if a.type == "dw" and by_width(int(a.params["from"]), int(a.params["to"])):
                return a.name, True
            if a.type == "pc" and a.params["from"] == "AXI4":
                return (a.name if burst == WRAP else None), True
        return None, False

    if burst == INCR:
        return None
    refused, ended = adapters(master, towards_port=False)
    if ended:
        return refused
    fabrics = {f.name: f for f in spec.fabrics}
    fabric = next(f for f in spec.fabrics if any(p.port == master for p in f.master_ports))
    if fabric.protocol != "AXI4":
        return None
    if bridges:  # every bridge from AXI4 ends the bursts, so only the first matters
        b = next(x for x in spec.bridges if x.name == bridges[0])
        up, down = fabric.data_width, fabrics[b.downstream].data_width
        if b.template in ("bag_axi2axil", "bag_axi2apb"):
            return b.name if by_width(up, down) else None
        assert b.template == "bag_axi2ahb", b.template
        return b.name if up != down and by_width(up, down) else None
    if slave is None:
        return None
    return adapters(slave, towards_port=True)[0]


def wrap_refusals(spec: Spec) -> dict[tuple[str, str], list[str]]:
    """{(AXI4 master, refusing width converter): the slaves and bridges on the master's fabric
    it refuses WRAP bursts to}, over every target the master may reach: each slave or bridge
    with a region on the fabric, less those Access_Rules deny it, and its default slave, which
    a miss or a denied access reaches whatever Access_Rules say. Refusals by conversion to
    AXI4-Lite or APB are left out (module docstring)."""
    out: dict[tuple[str, str], list[str]] = {}
    bridges = {b.name for b in spec.bridges}
    # Width converters: a dw adapter, and bag_axi2ahb (which refuses only between widths).
    width = {a.name for a in spec.adapters if a.type == "dw"}
    width |= {b.name for b in spec.bridges if b.template == "bag_axi2ahb"}
    for m in spec.masters:
        if m.protocol != "AXI4":
            continue
        fabric = next(f for f in spec.fabrics if any(p.port == m.name for p in f.master_ports))
        denied = {r.slave for r in fabric.access_rules if r.master == m.name and r.access == "none"}
        targets = {r.slave for r in fabric.addr_rules} - denied
        targets |= {d.default_slave for d in fabric.decode
                    if d.master == m.name and d.default_slave}  # fmt: skip
        for t in sorted(targets):
            through = (t,) if t in bridges else ()
            who = refusing_converter(spec, m.name, through, None if through else t, WRAP, 4)
            if who in width:
                out.setdefault((m.name, who), []).append(t)
    return out
